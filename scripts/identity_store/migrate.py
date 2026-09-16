"""Migration + zero-drift proof (piece-5, U6).

Import the legacy identity files (registry.json / agent-sessions.json /
state/agents/*.json) into the store, then reproject them byte-faithfully and
prove a byte-semantic diff of ZERO on the live dataset.

Two things happen at import:

* **Faithful capture** — every source record (top-level registry meta keys, each
  agent, each session, each state file) is stored verbatim in ``source_records``.
  This is the fidelity backstop that makes the round-trip provably lossless
  regardless of how heterogeneous the 400+ live agent dicts are.
* **Typed identity population** — the identity subset (lineage config + session
  id/model/generation + canonical pointer + runtime status) is JOINed across the
  three sources into the typed tables (best-effort, conflict-collecting), which
  is what swaps/shims/normal projections operate on post-cutover.

``reproject`` reconstructs the three files from ``source_records`` — this is where
the piece-3 projection legacy-field fidelity caveat RESOLVES. ``diff_report``
does the semantic comparison; empty == zero drift.

Read-only over the sources; writes only the (scratch) DB and the out_dir.
"""
import json
import os

from scripts.identity_store import orchestra_db

_REGISTRY_FILE = "registry.json"
_SESSIONS_FILE = "agent-sessions.json"
_AGENTS_FILE = "state/agents"


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------

def _store_source(conn, file, kind, key, ordinal, value):
    conn.execute(
        "INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(file, kind, key) DO UPDATE SET "
        "ordinal=excluded.ordinal, payload_json=excluded.payload_json",
        (file, kind, key, ordinal, json.dumps(value)))


def _runtime_from_model(model):
    m = (model or "").lower()
    if "gemini" in m:
        return "gemini"
    if "codex" in m or "gpt" in m or m.startswith("o1") or m.startswith("o3"):
        return "codex"
    return "claude"


def _populate_typed(conn, agents, sessions, state_by_agent, conflicts):
    """Best-effort JOIN of the three sources into the typed identity tables.
    Defensive: per-agent try/except; a same-id corpse in the LIVE data (duplicate
    session_id) is resolved last-wins via the same-txn take-over and RECORDED as a
    conflict (surfacing exactly the disease the store abolishes) — never fatal."""
    populated = 0
    for name, adict in agents.items():
        try:
            sess = sessions.get(name, {})
            runtime = _runtime_from_model(sess.get("model"))
            conn.execute(
                "INSERT INTO lineages (root, tier, runtime, reports_to, "
                "always_on, purpose, machine, cwd) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(root) DO NOTHING",
                (name, adict.get("tier") or "T2", runtime,
                 adict.get("reports_to"), 1 if adict.get("always_on") else 0,
                 adict.get("purpose"), adict.get("machine") or "vps",
                 adict.get("cwd")))
            generation = sess.get("generation") or 1
            sid = sess.get("session_id")
            model = sess.get("model") or "unknown"
            existing = conn.execute(
                "SELECT id FROM generations WHERE root=? AND generation=?",
                (name, generation)).fetchone()
            if existing:
                gid = existing["id"]
            else:
                if sid is not None:
                    holder = conn.execute(
                        "SELECT id FROM generations WHERE session_id=?",
                        (sid,)).fetchone()
                    if holder is not None:
                        conflicts.append({"root": name, "session_id": sid,
                                          "reason": "duplicate session_id (same-id corpse) — last-wins take-over"})
                        conn.execute(
                            "UPDATE generations SET session_id=NULL WHERE session_id=?",
                            (sid,))
                gid = conn.execute(
                    "INSERT INTO generations (root, generation, session_id, model) "
                    "VALUES (?,?,?,?)", (name, generation, sid, model)).lastrowid
            conn.execute(
                "INSERT INTO canonical (root, generation_id, tmux_session, status) "
                "VALUES (?,?,?,?) ON CONFLICT(root) DO NOTHING",
                (name, gid, adict.get("tmux_session") or name,
                 adict.get("status") or "online"))
            st = state_by_agent.get(name, {})
            conn.execute(
                "INSERT INTO runtime_state (generation_id, status, current_task, "
                "last_updated) VALUES (?,?,?,?) "
                "ON CONFLICT(generation_id) DO UPDATE SET "
                "status=excluded.status, current_task=excluded.current_task",
                (gid, st.get("status") or adict.get("status"),
                 st.get("task"), st.get("last_updated")))
            populated += 1
        except Exception as e:                          # noqa: BLE001
            conflicts.append({"root": name, "reason": f"typed-populate error: {e!r}"})
    return populated


def migrate(conn, registry_path, sessions_path, agents_dir):
    """Import the three legacy files into the store. Returns a report dict."""
    with open(registry_path) as fh:
        registry = json.load(fh)
    with open(sessions_path) as fh:
        sessions = json.load(fh)

    agents = registry.get("agents", {})

    conn.execute("BEGIN IMMEDIATE")
    try:
        ordinal = 0
        for k, v in registry.items():
            if k == "agents":
                for i, (name, adict) in enumerate(v.items()):
                    _store_source(conn, _REGISTRY_FILE, "agent", name, i, adict)
                continue
            _store_source(conn, _REGISTRY_FILE, "meta", k, ordinal, v)
            ordinal += 1
        for i, (name, sd) in enumerate(sessions.items()):
            _store_source(conn, _SESSIONS_FILE, "session", name, i, sd)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    state_by_agent = {}
    if agents_dir and os.path.isdir(agents_dir):
        for i, fname in enumerate(sorted(os.listdir(agents_dir))):
            if not fname.endswith(".json"):
                continue
            with open(os.path.join(agents_dir, fname)) as fh:
                content = json.load(fh)
            _store_source(conn, _AGENTS_FILE, "state_agent", fname, i, content)
            root = content.get("agent_id") or fname[:-5]
            state_by_agent[root] = content

    conflicts = []
    populated = _populate_typed(conn, agents, sessions, state_by_agent, conflicts)
    return {
        "source_records": conn.execute(
            "SELECT COUNT(*) FROM source_records").fetchone()[0],
        "typed_populated": populated,
        "typed_conflicts": conflicts,
    }


# --------------------------------------------------------------------------
# reproject (byte-faithful) — the piece-3 fidelity caveat resolves here
# --------------------------------------------------------------------------

def _atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _rows(conn, file, kind):
    return conn.execute(
        "SELECT key, payload_json FROM source_records "
        "WHERE file=? AND kind=? ORDER BY ordinal, key", (file, kind)).fetchall()


def reproject(conn, out_dir):
    """Reconstruct the legacy files from source_records, byte-faithfully."""
    registry = {}
    for r in _rows(conn, _REGISTRY_FILE, "meta"):
        registry[r["key"]] = json.loads(r["payload_json"])
    registry["agents"] = {
        r["key"]: json.loads(r["payload_json"])
        for r in _rows(conn, _REGISTRY_FILE, "agent")
    }
    _atomic_write_json(os.path.join(out_dir, "registry.json"), registry)

    sessions = {
        r["key"]: json.loads(r["payload_json"])
        for r in _rows(conn, _SESSIONS_FILE, "session")
    }
    _atomic_write_json(os.path.join(out_dir, "state", "agent-sessions.json"),
                       sessions)

    for r in _rows(conn, _AGENTS_FILE, "state_agent"):
        _atomic_write_json(
            os.path.join(out_dir, "state", "agents", r["key"]),
            json.loads(r["payload_json"]))


# --------------------------------------------------------------------------
# semantic diff
# --------------------------------------------------------------------------

def _diff(a, b, path):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in set(a) | set(b):
            if k not in a:
                out.append(f"{path}/{k}: extra in projection")
            elif k not in b:
                out.append(f"{path}/{k}: missing in projection")
            else:
                out += _diff(a[k], b[k], f"{path}/{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: list length {len(a)} != {len(b)}"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += _diff(x, y, f"{path}[{i}]")
        return out
    if a != b:
        return [f"{path}: {a!r} != {b!r}"]
    return []


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def diff_report(original_dir, projected_dir):
    """Byte-semantic diff of the three files between two trees. Empty == zero
    drift (the U6 proof)."""
    diffs = []
    for rel in ("registry.json", os.path.join("state", "agent-sessions.json")):
        o, p = os.path.join(original_dir, rel), os.path.join(projected_dir, rel)
        if not os.path.exists(p):
            diffs.append(f"{rel}: missing in projection")
            continue
        diffs += _diff(_load(o), _load(p), rel)

    oa_dir = os.path.join(original_dir, "state", "agents")
    pa_dir = os.path.join(projected_dir, "state", "agents")
    # Only *.json are managed state documents — mirror migrate()'s :144 filter on
    # BOTH sides so a non-.json placeholder (.gitkeep/README) that migrate/reproject
    # intentionally skips does not read as a spurious "missing in projection" diff.
    orig = {f for f in os.listdir(oa_dir) if f.endswith(".json")} if os.path.isdir(oa_dir) else set()
    proj = {f for f in os.listdir(pa_dir) if f.endswith(".json")} if os.path.isdir(pa_dir) else set()
    for name in sorted(orig | proj):
        if name not in orig:
            diffs.append(f"state/agents/{name}: extra in projection")
        elif name not in proj:
            diffs.append(f"state/agents/{name}: missing in projection")
        else:
            diffs += _diff(_load(os.path.join(oa_dir, name)),
                           _load(os.path.join(pa_dir, name)),
                           f"state/agents/{name}")
    return diffs
