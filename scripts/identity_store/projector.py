"""Strangler projector — regenerate legacy read artifacts from the identity DB.

DP-U1(b): the SQLite store is the single write-truth; ~130 legacy readers keep
consuming ``registry.json`` / ``agent-sessions.json`` / ``state/agents/*.json``
unmodified. The projector regenerates those files as READ-ONLY snapshots so a
reader can never observe a torn identity state.

Core guarantees implemented here (piece-3a):

* **One-snapshot triple (U10):** a single read transaction produces one snapshot
  id; every projection file in the pass is stamped with it, so two projections
  from different cycles cannot be mixed by a reader.
* **Atomic write (U15):** each file is written to a sibling ``*.tmp`` then
  ``os.replace``d into place — a reader sees the whole old file or the whole new
  file, never a partial one; no temp files are left behind.
"""
import hashlib
import json
import os
import time
import uuid

from scripts.identity_store import orchestra_db

PROJECTION_HEADER_KEY = "_projection"
PROJECTOR_SIGNATURE = "orchestra-projector"
_MANIFEST_NAME = ".projector-manifest.json"
_QUARANTINE_DIR = ".quarantine"


class ForeignIdentityError(Exception):
    """Raised when an identity-establishing foreign write is detected and the
    policy is HARD-FAIL (or adoption is impossible). The projector does NOT
    overwrite the foreign file — overwriting would silently orphan the live
    agent the write established. The foreign bytes are preserved to quarantine."""

    def __init__(self, rel, identities):
        super().__init__(f"identity-establishing foreign write in {rel}: {identities}")
        self.rel = rel
        self.identities = identities


def _read_snapshot(conn):
    """Read the whole identity state inside ONE read transaction so the three
    projections cannot straddle a concurrent write (U10). Returns a plain dict."""
    conn.execute("BEGIN")
    try:
        lineages = {
            r["root"]: dict(r)
            for r in conn.execute("SELECT * FROM lineages").fetchall()
        }
        agents = {}
        for r in conn.execute(
            "SELECT l.root, l.tier, l.runtime, l.reports_to, l.always_on, "
            "l.purpose, l.machine, l.cwd, c.status AS canonical_status, "
            "c.tmux_session, g.id AS generation_id, g.generation, g.session_id, "
            "g.model, g.conversation_path, "
            "rs.status AS runtime_status, rs.current_task, rs.last_updated, "
            "rs.last_active, rs.tags_json, rs.memory_scope_json "
            "FROM canonical c "
            "JOIN lineages l ON l.root = c.root "
            "JOIN generations g ON g.id = c.generation_id "
            "LEFT JOIN runtime_state rs ON rs.generation_id = c.generation_id"
        ).fetchall():
            agents[r["root"]] = dict(r)
    finally:
        conn.execute("COMMIT")  # read-only; releases the snapshot
    return {"lineages": lineages, "agents": agents}


def _header(snapshot_id, generated_at):
    return {
        PROJECTION_HEADER_KEY: {
            "snapshot_id": snapshot_id,
            "generated_at": generated_at,
            "projector": PROJECTOR_SIGNATURE,
        }
    }


def _build_projections(snapshot, snapshot_id, generated_at):
    """Return {relative_path: json_obj}. Every object carries the SAME snapshot
    header (U10). Shapes are strangler-plausible; exact legacy-field fidelity is
    proven by the piece-5 zero-drift round (U6)."""
    agents = snapshot["agents"]

    registry = _header(snapshot_id, generated_at)
    registry["agents"] = {
        root: {
            "tier": a["tier"],
            "runtime": a["runtime"],
            "machine": a["machine"],
            "generation": a["generation"],
            "session_id": a["session_id"],
            "status": a["canonical_status"],
        }
        for root, a in agents.items()
    }

    sessions = _header(snapshot_id, generated_at)
    for root, a in agents.items():
        sessions[root] = {
            "session_id": a["session_id"],
            "generation": a["generation"],
            "model": a["model"],
            "conversation_path": a["conversation_path"],
        }

    files = {"registry.json": registry, "agent-sessions.json": sessions}
    for root, a in agents.items():
        blob = _header(snapshot_id, generated_at)
        blob.update({
            "agent_id": root,
            "tier": a["tier"],
            "machine": a["machine"],
            "status": a["runtime_status"] or a["canonical_status"],
            "current_task": a["current_task"],
            "last_updated": a["last_updated"],
        })
        files[os.path.join("state", "agents", f"{root}.json")] = blob
    return files


def _atomic_write_json(path, obj):
    """temp+rename write. os.replace is atomic on the same filesystem — a reader
    either sees the previous file or the new one, never a partial. Returns the
    exact bytes written (for manifest checksumming)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = json.dumps(obj, indent=2, sort_keys=True).encode()
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return data


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest_path(out_dir):
    return os.path.join(out_dir, _MANIFEST_NAME)


def _load_manifest(out_dir):
    try:
        with open(_manifest_path(out_dir)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_manifest(out_dir, manifest):
    _atomic_write_json(_manifest_path(out_dir), manifest)


def _quarantine(out_dir, rel, data: bytes, snapshot_id):
    """Preserve foreign bytes BEFORE the projector overwrites them (ob rider:
    the alarm without the preserved copy loses the evidence)."""
    qdir = os.path.join(out_dir, _QUARANTINE_DIR)
    os.makedirs(qdir, exist_ok=True)
    name = f"{os.path.basename(rel)}.{snapshot_id}"
    with open(os.path.join(qdir, name), "wb") as fh:
        fh.write(data)


def _extract_identities(rel, obj):
    """Pull candidate identities from a foreign projection so U16 can decide
    whether the write ESTABLISHES a new identity. Best-effort per file shape."""
    if not isinstance(obj, dict):
        return []
    base = os.path.basename(rel)
    out = []
    if base == "registry.json":
        for root, a in (obj.get("agents") or {}).items():
            if isinstance(a, dict):
                out.append({"root": root, **{
                    k: a.get(k) for k in
                    ("tier", "runtime", "machine", "generation", "session_id", "model")}})
    elif base == "agent-sessions.json":
        for root, s in obj.items():
            if root == PROJECTION_HEADER_KEY or not isinstance(s, dict):
                continue
            out.append({"root": root, "generation": s.get("generation"),
                        "session_id": s.get("session_id"), "model": s.get("model")})
    else:  # state/agents/<root>.json
        root = obj.get("agent_id")
        if root:
            out.append({"root": root, "tier": obj.get("tier"),
                        "machine": obj.get("machine")})
    return out


def _identity_missing_from_db(conn, ident) -> bool:
    """An identity is ESTABLISHING if the DB lacks its lineage, or lacks the
    specific (root, generation) — i.e. a spawn/recovery write the DB never saw."""
    root = ident.get("root")
    if not root:
        return False
    if conn.execute("SELECT 1 FROM lineages WHERE root=?", (root,)).fetchone() is None:
        return True
    gen = ident.get("generation")
    if gen is not None and conn.execute(
            "SELECT 1 FROM generations WHERE root=? AND generation=?",
            (root, gen)).fetchone() is None:
        return True
    return False


def _handle_foreign_writes(conn, out_dir, projections, manifest, snapshot_id,
                           alarm, on_foreign_identity):
    """Before overwriting, compare each existing projection to the projector's
    manifest. A mismatch is a FOREIGN write: quarantine the bytes (before
    overwrite), alarm, and if it establishes an identity the DB lacks either
    ADOPT it (own txn) or HARD-FAIL — never silent-revert. Returns True iff any
    identity was adopted (caller must re-snapshot)."""
    adopted_any = False
    for rel in projections:
        path = os.path.join(out_dir, rel)
        if rel not in manifest or not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            cur = fh.read()
        if _sha(cur) == manifest[rel]:
            continue  # projector-authored, untouched
        _quarantine(out_dir, rel, cur, snapshot_id)
        alarm({"kind": "foreign-write", "file": rel})
        try:
            foreign_obj = json.loads(cur)
        except ValueError:
            foreign_obj = None
        establishing = [i for i in _extract_identities(rel, foreign_obj)
                        if _identity_missing_from_db(conn, i)]
        if not establishing:
            continue  # stray edit to an existing row; projector truth restored
        alarm({"kind": "foreign-identity", "file": rel, "identities": establishing})
        if on_foreign_identity != "adopt":
            raise ForeignIdentityError(rel, establishing)
        for ident in establishing:
            orchestra_db.adopt_identity(conn, ident, now=None)
            alarm({"kind": "identity-adopted", "root": ident.get("root")})
            adopted_any = True
    return adopted_any


def project(conn, out_dir, now=None, alarm=None, on_foreign_identity="hard_fail"):
    """Regenerate all projections from one DB read-snapshot into ``out_dir``.

    Before overwriting, foreign writes (edits the projector did not author) are
    detected against the manifest, quarantined, and alarmed; an identity-
    establishing foreign write is ADOPTed or HARD-FAILed per
    ``on_foreign_identity`` ('adopt' | 'hard_fail') — never silent-reverted.
    Returns ``{snapshot_id, generated_at, files}``.
    """
    alarm = alarm or (lambda _evt: None)
    generated_at = now if now is not None else time.time()
    snapshot_id = uuid.uuid4().hex
    snapshot = _read_snapshot(conn)
    projections = _build_projections(snapshot, snapshot_id, generated_at)

    manifest = _load_manifest(out_dir)
    adopted = _handle_foreign_writes(conn, out_dir, projections, manifest,
                                     snapshot_id, alarm, on_foreign_identity)
    if adopted:
        # re-snapshot so the regenerated projections include adopted identities
        snapshot = _read_snapshot(conn)
        projections = _build_projections(snapshot, snapshot_id, generated_at)

    written = []
    new_manifest = {}
    for rel, obj in projections.items():
        path = os.path.join(out_dir, rel)
        data = _atomic_write_json(path, obj)
        written.append(path)
        new_manifest[rel] = _sha(data)
    _save_manifest(out_dir, new_manifest)
    return {"snapshot_id": snapshot_id, "generated_at": generated_at,
            "files": written}


def check_liveness(out_dir, now, max_age_s, alarm):
    """U12 — a staleness bound on the projector, its own SPOF guard. If the
    newest projection (registry.json) is missing (projector crashed before ever
    writing) or older than ``max_age_s`` (projector frozen/stuck), fire ``alarm``
    and return False so the fleet PAGES instead of silently reading a frozen
    view. ``alarm`` is an injected sink receiving a detail dict."""
    reg_path = os.path.join(out_dir, "registry.json")
    try:
        with open(reg_path) as fh:
            header = json.load(fh).get(PROJECTION_HEADER_KEY, {})
    except (OSError, ValueError):
        alarm({"kind": "projector-missing", "path": reg_path})
        return False
    generated_at = header.get("generated_at")
    if generated_at is None:
        alarm({"kind": "projector-missing", "path": reg_path})
        return False
    age = now - generated_at
    if age > max_age_s:
        alarm({"kind": "projector-stale", "age": age, "max_age_s": max_age_s})
        return False
    return True


# ==========================================================================
# item-1b — the FAITHFUL LIVE merge-projector (Part A + Part B)
# ==========================================================================
#
# Unlike ``project()`` (a strangler shape over the typed subset), ``project_faithful``
# serves each identity record's FULL LIVE document from ``source_records`` verbatim,
# using the typed identity index ONLY to decide the projected SHAPE per operation.
# This is the design the §0 by-effect field enumeration forced: the typed schema
# models a small fraction of the live fields, and the UNMODELED remainder is largely
# MUTABLE — a typed-columns-over-a-frozen-base overlay would SILENTLY FREEZE that
# whole class (the exact corruption the store exists to kill). Serving the live
# document keeps every field live by construction. Writers keep the document current
# in the SAME txn as their typed write (DP-A2), so index and document never diverge.
#
# The artifacts stay BYTE-FAITHFUL (no ``_projection`` header injected — else the
# M1/runbook-G2 zero-drift proof fails); the snapshot id + liveness live in a
# SIDECAR (``FAITHFUL_META_NAME``) the monitor reads.

FAITHFUL_META_NAME = ".projection-meta.json"
FAITHFUL_SIGNATURE = "orchestra-faithful-projector"

_REGISTRY_FILE = "registry.json"
_SESSIONS_REL = os.path.join("state", "agent-sessions.json")


def _read_faithful_snapshot(conn, *, skip_bad=False, warn=None):
    """ONE read transaction (U10): read the live document store (source_records)
    and the typed identity index (lineages/canonical/generations) so the whole
    triple projects from a single consistent snapshot — a concurrent writer can
    never make the triple straddle a commit.

    ``skip_bad`` (default False → the projector daemon's behavior is UNCHANGED): a
    row whose ``payload_json`` fails json.loads (a concurrent writer can transiently
    leave an empty/partial value under a mode=ro read) is SKIPPED with a warn rather
    than raising, so a single bad row cannot fail the whole build. The READER paths
    (registry_agent_db / build_agent_meta_db) opt into this so one transient row can
    never make them fail-safe-to-flat and lose an otherwise-good agent (BUG3, gm
    msg_1464f5cf); the live daemon keeps the strict raise (default)."""
    conn.execute("BEGIN")
    try:
        docs = {"meta": {}, "agent": {}, "session": {}, "state_agent": {}}
        for r in conn.execute(
                "SELECT kind, key, ordinal, payload_json FROM source_records"):
            if skip_bad:
                try:
                    val = json.loads(r["payload_json"])
                except (ValueError, TypeError) as e:
                    if warn:
                        warn(f"skipping source_records row with unparseable "
                             f"payload_json: kind={r['kind']} key={r['key']} "
                             f"({type(e).__name__}) — a concurrent writer likely left "
                             f"it mid-update; the good rows still project")
                    continue
            else:
                val = json.loads(r["payload_json"])
            docs.setdefault(r["kind"], {})[r["key"]] = (r["ordinal"], val)
        lineages = {r["root"]: dict(r)
                    for r in conn.execute("SELECT * FROM lineages")}
        canonical = {r["root"]: dict(r)
                     for r in conn.execute("SELECT * FROM canonical")}
        generations = [dict(r) for r in conn.execute("SELECT * FROM generations")]
    finally:
        conn.execute("COMMIT")  # read-only; releases the snapshot
    return {"docs": docs, "lineages": lineages, "canonical": canonical,
            "generations": generations}


def _alias_payload(root, gen, lineage, root_doc):
    """DP-B2 — the exact 12-field provisional-alias payload, reproduced from the
    provisional generation row + its lineage (+ the root's live doc for the shared
    system_prompt, which the typed schema does not model). NO session_id: the
    provisional successor's sid is attributed later."""
    alias = f"{root}-g{gen['generation']}"
    return {
        "name": alias,
        "tier": lineage["tier"],
        "machine": lineage["machine"],
        "runtime": lineage["runtime"],
        "model": gen["model"],
        "cwd": lineage["cwd"],
        "tmux_session": alias,
        "generation": gen["generation"],
        "lineage_root": root,
        "always_on": bool(lineage["always_on"]),
        "system_prompt": (root_doc or {}).get("system_prompt"),
        "status": "provisioning",
    }


def _synth_archive(root, gen):
    """Fallback archive shape when the rewired rotation writer has not (yet)
    persisted a full ``<root>-gen<N>`` document — minimal retired identity."""
    return {"name": root, "lineage_root": root, "generation": gen["generation"],
            "session_id": gen.get("session_id"), "status": "retired"}


_ARCHIVE_IDENTITY_FIELDS = ("session_id", "resume_command")
_LIVE_IDENTITY_FIELDS = ("session_id", "generation", "model", "resume_command")


def _db_first_live_identity(doc, gen):
    """gm msg_816d4940 (by effect 2026-09-16 13:05 Tulum: after swap_generation moved task-gm
    canonical to gen 2 / sid 70b7024d, registry.json still read generation 1 / session_id None
    and agent-sessions.json the OLD sid — the live canonical row was the document verbatim).
    Mirror of the archive rule: identity fields on a LIVE canonical row come from the canonical
    generation row when it has them; the document only fills gaps; NULL/'unknown' never blank
    a document value."""
    out = dict(doc)
    for f in _LIVE_IDENTITY_FIELDS:
        if f not in out:
            continue            # M1 zero-drift: never ADD a key the document never carried
        v = gen.get(f)
        if v is None or v == "" or (f == "model" and str(v).lower() == "unknown"):
            continue            # a NULL/'unknown' row value never blanks the document
        out[f] = v
    return out


def _db_first_archive_identity(doc, gen):
    """gm msg_e7d7f734 (by effect: relational-intent-gen1 projected session_id None while the
    generations row held 01a08c8c + its resume_command): identity fields on a swap-retired
    archive are DB-FIRST — the typed generation row's session_id / resume_command win when
    present; the persisted document only fills a gap (never blanked by a NULL row value)."""
    out = dict(doc)
    for f in _ARCHIVE_IDENTITY_FIELDS:
        v = gen.get(f)
        if v:
            out[f] = v
        elif f not in out:
            out[f] = None
    return out


def _archive_status_for(gen):
    """Item (b), gm msg_b1429f36: a swap-retired predecessor archive is a RESUMABLE
    parked identity when its generation still carries a resume_command; only a truly
    non-resumable archive is 'retired'. Decided from the TYPED generations row (the
    promote finalizes resume_command onto it DB-first), so BOTH projections apply
    the SAME rule and can never disagree (M1 per-file blindness cannot see a
    registry-vs-sessions split)."""
    return "parked" if gen.get("resume_command") else "retired"


def _archive_statuses(snap):
    """{ '<root>-gen<N>': status } for every swap-retired predecessor archive (a
    retired generation whose root still has a canonical pointer)."""
    canonical = snap["canonical"]
    out = {}
    for gen in snap["generations"]:
        if gen["root"] in canonical and gen["retired_at"] is not None:
            out[f"{gen['root']}-gen{gen['generation']}"] = _archive_status_for(gen)
    return out


def _build_faithful_registry(snap):
    """registry.json: meta keys from their live meta documents (the sparse
    ``_canonical`` pin marker, ``_retired_agents``, version/machines/last_updated
    are all live meta docs — NOT regenerable from the typed ``canonical`` table
    without drift, per the by-effect finding); ``_provisional`` DERIVED from live
    non-canonical generations. ``agents`` per the operation SHAPE TABLE."""
    docs, lineages = snap["docs"], snap["lineages"]
    canonical, generations = snap["canonical"], snap["generations"]

    registry = {}
    for key, (_ordinal, val) in docs["meta"].items():
        registry[key] = val

    agents = {}
    # (1) live canonical members: the live agent document, identity fields DB-FIRST from
    # the canonical generation row (gm msg_816d4940; same rule as the archives below).
    canonical_gen_ids = {c["generation_id"] for c in canonical.values()}
    gen_by_id = {g["id"]: g for g in generations}
    for root, crow in canonical.items():
        doc = docs["agent"].get(root)
        base = doc[1] if doc is not None else {"name": root, "lineage_root": root}
        gen = gen_by_id.get(crow["generation_id"])
        agents[root] = _db_first_live_identity(base, gen) if gen else base

    # (2) swap-retired predecessors (KEPT) + (3) provisional aliases.
    provisional = {}
    for gen in generations:
        root = gen["root"]
        if root not in canonical:
            # root has no canonical pointer (park-idle full-retire): REMOVED —
            # a retired generation here is NOT resurrected as an archive.
            continue
        if gen["retired_at"] is not None:
            # swap-retired predecessor -> <root>-gen<N> archive. Status by the ONE
            # rule (item b): 'parked' if the archive is resumable (resume_command on
            # the gen row) else 'retired' — the SAME value the sessions projection
            # uses, so registry and sessions never disagree.
            key = f"{root}-gen{gen['generation']}"
            if key in agents:
                continue  # a standalone migrated archive lineage already owns it
            arch_status = _archive_status_for(gen)
            doc = docs["agent"].get(key)
            base = doc[1] if doc is not None else _synth_archive(root, gen)
            agents[key] = {**_db_first_archive_identity(base, gen), "status": arch_status}
        elif gen["id"] not in canonical_gen_ids:
            # live, non-canonical generation -> provisional alias <root>-g<N>.
            key = f"{root}-g{gen['generation']}"
            if key in agents:
                continue
            root_doc = docs["agent"].get(root)
            agents[key] = _alias_payload(
                root, gen, lineages.get(root, {}),
                root_doc[1] if root_doc is not None else None)
            provisional[key] = {"lineage_root": root,
                                "generation": gen["generation"]}

    registry["agents"] = agents
    registry["_provisional"] = provisional
    return registry


def _build_faithful_sessions(snap):
    """agent-sessions.json: each live session's document verbatim. Provisional
    aliases are REGISTRY-ONLY (P4) — they never get a sessions entry, which is
    automatic here because sessions come only from the session documents.

    Item (b): a swap-retired ARCHIVE session doc gets its status overwritten with the
    SAME resumable-archive rule the registry projection applies, so the two files
    can never split ('parked' resumable / 'retired' otherwise)."""
    sessions = {key: doc for key, (_o, doc) in snap["docs"]["session"].items()}
    canonical, lineages = snap["canonical"], snap.get("lineages") or {}
    # live canonical session rows: identity DB-first from the canonical generation row
    # (gm msg_816d4940) — the same rule the registry projection applies above.
    gen_by_id = {g["id"]: g for g in snap["generations"]}
    for root, crow in canonical.items():
        gen = gen_by_id.get(crow["generation_id"])
        row = sessions.get(root)
        if gen and isinstance(row, dict):
            sessions[root] = _db_first_live_identity(row, gen)
    for gen in snap["generations"]:
        if gen["root"] in canonical and gen["retired_at"] is not None:
            key = f"{gen['root']}-gen{gen['generation']}"
            row = sessions.get(key)
            if not isinstance(row, dict):
                # Sessions parity (gm msg_347d7ee2): the BG swap path persists NO session
                # document for a swap-retired predecessor, so agent-sessions.json lacked the
                # archive row registry.json carried. Synthesize it from the generation row +
                # lineage (mirror of the registry's _synth_archive), DB-first identity.
                lin = lineages.get(gen["root"]) or {}
                row = {"name": key, "lineage_root": gen["root"],
                       "generation": gen["generation"], "runtime": lin.get("runtime"),
                       "tier": lin.get("tier"), "machine": lin.get("machine"),
                       "model": gen.get("model")}
            sessions[key] = {**_db_first_archive_identity(row, gen),
                             "status": _archive_status_for(gen)}
    return sessions


def _build_faithful_state_blob(snap, key):
    return snap["docs"]["state_agent"][key][1]


def project_faithful(conn, out_dir, now=None):
    """Regenerate the three legacy artifacts as BYTE-FAITHFUL LIVE projections from
    one DB read-snapshot (U10), each written temp+rename (U15). Serves full live
    documents (nothing typed-frozen) with typed-index shape decisions. Writes a
    sidecar (``FAITHFUL_META_NAME``) carrying the snapshot id + generated_at for the
    liveness monitor; the artifacts themselves stay header-free so the M1/G2
    zero-drift proof holds. Returns ``{snapshot_id, generated_at, files}``."""
    generated_at = now if now is not None else time.time()
    snapshot_id = uuid.uuid4().hex
    snap = _read_faithful_snapshot(conn)

    registry = _build_faithful_registry(snap)
    sessions = _build_faithful_sessions(snap)

    written = []
    written.append(os.path.join(out_dir, _REGISTRY_FILE))
    _atomic_write_json(written[-1], registry)
    written.append(os.path.join(out_dir, _SESSIONS_REL))
    _atomic_write_json(written[-1], sessions)
    for key in snap["docs"]["state_agent"]:
        path = os.path.join(out_dir, "state", "agents", key)
        _atomic_write_json(path, _build_faithful_state_blob(snap, key))
        written.append(path)

    _atomic_write_json(
        os.path.join(out_dir, FAITHFUL_META_NAME),
        {"snapshot_id": snapshot_id, "generated_at": generated_at,
         "projector": FAITHFUL_SIGNATURE})
    return {"snapshot_id": snapshot_id, "generated_at": generated_at,
            "files": written}


def check_faithful_liveness(out_dir, now, max_age_s, alarm):
    """U12 for the faithful projector — staleness bound read from the SIDECAR
    (the artifacts are header-free). Missing/frozen sidecar -> alarm + False so the
    fleet PAGES instead of reading a stale view."""
    meta_path = os.path.join(out_dir, FAITHFUL_META_NAME)
    try:
        with open(meta_path) as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        alarm({"kind": "projector-missing", "path": meta_path})
        return False
    generated_at = meta.get("generated_at")
    if generated_at is None:
        alarm({"kind": "projector-missing", "path": meta_path})
        return False
    age = now - generated_at
    if age > max_age_s:
        alarm({"kind": "projector-stale", "age": age, "max_age_s": max_age_s})
        return False
    return True


class Projector:
    """Stateful driver that coalesces rapid ``runtime_state`` heartbeat marks
    into at most one projection per debounce window (U15). A heartbeat storm
    marks the projector dirty many times; only the first mark past the window
    triggers an actual write, bounding projection I/O. ``clock`` is injectable
    for deterministic tests."""

    def __init__(self, conn, out_dir, debounce_s=2.0, clock=time.monotonic):
        self._conn = conn
        self._out_dir = out_dir
        self._debounce_s = debounce_s
        self._clock = clock
        self._dirty = False
        self._last_project = None

    def mark_dirty(self):
        self._dirty = True

    def maybe_project(self, force=False):
        """Project iff dirty and the debounce window has elapsed (or force).
        Returns True iff a projection was written this call."""
        if not self._dirty and not force:
            return False
        now = self._clock()
        if (not force and self._last_project is not None
                and (now - self._last_project) < self._debounce_s):
            return False  # coalesce: within the debounce window
        project(self._conn, self._out_dir)
        self._dirty = False
        self._last_project = now
        return True
