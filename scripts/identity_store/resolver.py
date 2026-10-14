"""DEC-1788554471 — the DB-first READER seam for agent identity under cutover.

The flat registry.json / agent-sessions.json are PROJECTIONS of
state/orchestra-registry.db (live since the 09-02 cutover). They are git-tracked on
ONE working tree shared by ~40 sessions on DIFFERENT branches, so a foreign
`git checkout` overwrites them with a branch snapshot that can LACK a brand-new
agent — flapping it `unregistered` / double-chip until the next projector pass. The
DB never flaps. The readers (message-router resolve + the dashboard) must therefore
read name->identity DB-FIRST.

Contract (congruence C1/C2, mirroring the gen_resolve.py precedent):
- SOURCE (C1): build the meta map from the ``source_records`` (kind=`session`)
  VERBATIM documents — the ``project_faithful`` path — NOT the typed
  canonical<->generations JOIN. The typed schema has no ``succeeded_by`` /
  ``superseded_by`` column and ``canonical.status`` never == ``"provisioning"``; a
  JOIN-sourced meta would DROP succession pointers (holding retired-predecessor mail
  instead of forwarding it) and mis-fire the provisioning guard. The session
  documents ARE today's agent-sessions.json entries, so every routing-relevant field
  is preserved intact AND flap-immune.
- READ-ONLY: open ``mode=ro`` (URI), 5s timeout, closed immediately. Never
  write-locks the identity store; PRE-txn only.
- UNION (C2): union DB-wins-per-key OVER the flat map. Flat/live supplies any key the
  DB lacks, so the map can NEVER shrink below the live set (no partial-DB starvation);
  the DB overrides shared keys (the flap fix). Fail-loud alarm on wholly-empty/absent
  DB and on a materially-below-flat key count. Per-key DB-wins skew is SILENT — it is
  the designed normal path (the flat flaps; the DB is truth), not an incident.
- NEVER fabricate.
"""
import json
import os
import sqlite3
import sys
import time

# Fraction of the flat key count below which the DB canonical set is treated as a
# torn/partial migration (diagnostic alarm; the union still preserves flat keys).
_COUNT_ANOMALY_FLOOR = 0.5

# The router resolves on every pass (~1/min). An alarm condition is reported once per
# episode and again at most once a day while it lasts; a resolve that no longer sees it
# ends the episode. Without this a fresh install sent gm the same alert every minute.
_ALARM_REMIND_S = 24 * 3600
_ALARM_STATE_REL = os.path.join("state", "readers-db-first-alarms.json")
_now = time.time


def _alarm_state(path: str) -> dict:
    try:
        with open(path) as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_alarm_state(path: str, state: dict) -> None:
    try:
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except OSError:
        pass                     # unrecordable: the next pass alarms again (loud, never silent)


def _episodes(orch, active: dict, alarm) -> None:
    """Report each active condition (kind -> message) once per episode, then at most once per
    _ALARM_REMIND_S; forget the kinds that are no longer active, so their next occurrence is
    reported at once."""
    path = os.path.join(str(orch), _ALARM_STATE_REL)
    lock = None
    try:                         # two router passes at once read-modify-write one record
        import fcntl
        lock = open(f"{path}.lock", "a")
        fcntl.flock(lock, fcntl.LOCK_EX)
    except (OSError, ImportError):
        if lock is not None:
            lock.close()
        lock = None              # no lock: worst case one extra alarm, never a lost one
    try:
        _episodes_locked(path, active, alarm)
    finally:
        if lock is not None:
            lock.close()


def _episodes_locked(path: str, active: dict, alarm) -> None:
    state = _alarm_state(path)
    now = _now()
    changed = False
    for kind in [k for k in state if k not in active]:
        del state[kind]
        changed = True
    for kind, msg in active.items():
        last = state.get(kind, {}).get("last_sent") if isinstance(state.get(kind), dict) else None
        if isinstance(last, (int, float)) and now - last < _ALARM_REMIND_S:
            continue
        if alarm(msg) is False:  # not delivered: not recorded, so the next pass retries
            continue
        state[kind] = {"last_sent": now}
        changed = True
    if changed:
        _save_alarm_state(path, state)


def _default_alarm(msg: str) -> bool:
    """Incident-grade surfacing: stderr + best-effort gm message. Imported inside so
    the resolver keeps no hard msg_store dependency (INERT on the unarmed path).
    Returns False when the gm message was not delivered, so the episode record does not
    count it as sent and the next pass tries again."""
    print(f"[readers-db-first ALARM] {msg}", file=sys.stderr)
    try:
        import subprocess
        proc = subprocess.run(
            ["python3", os.path.join(os.environ.get("ORCHESTRA_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                          "msg_store.py"), "send",   # CODE (checkout); data via ORCHESTRA_DIR
             "--from", "readers-db-first", "--to", "gm", "--type", "alert",
             "--subject", "identity projection skew (readers-db-first)",
             "--body", msg],
            timeout=10, capture_output=True)
        return getattr(proc, "returncode", 0) == 0
    except Exception:  # noqa: BLE001 — alarm is best-effort beyond stderr
        return False


def _transient_warn(msg: str) -> None:
    """STDERR-ONLY warn for an EXPECTED transient condition — a source_records row a
    concurrent writer left mid-update (empty/partial payload_json) under a mode=ro read.
    On a live ~40-writer store this is normal churn, NOT an incident, so it must NEVER
    fire the gm msg_store alarm (that flooded gm ~1/min — gm msg_786044c1; same class as
    the per-key-skew alarm silenced @cf02113c7). A genuinely stuck/corrupt row surfaces
    elsewhere (the projector daemon's strict raise + its staleness monitor)."""
    sys.stderr.write(f"[readers-db-first skip] {msg}\n")


def _db_path(orch: str) -> str:
    return os.path.join(str(orch), "state", "orchestra-registry.db")


def build_agent_meta_db(orch, *, alarm=_default_alarm) -> dict:
    """Return the DB-derived agent meta map (== the agent-sessions.json shape) read
    from the source_records session documents, READ-ONLY. Returns ``{}`` when the DB
    is absent/unreadable/empty (the caller unions it over the flat file). Never
    writes, never raises for an absent/broken DB."""
    dbp = _db_path(orch)
    if not os.path.exists(dbp):
        return {}
    try:
        # mode=ro: strictly read-only (cannot even write the WAL pragma). isolation
        # _level=None so the projector's explicit BEGIN/COMMIT read-snapshot drives.
        conn = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None
        try:
            # Reuse the tested faithful-projector session builder so the meta shape
            # and succession semantics are never forked. skip_bad: a transient
            # unparseable source_records row (concurrent writer mid-update) skips that
            # one row rather than dropping the whole DB meta to the flat fallback.
            from scripts.identity_store import projector
            snap = projector._read_faithful_snapshot(conn, skip_bad=True, warn=_transient_warn)
            return projector._build_faithful_sessions(snap)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — any failure to read the store is "unreadable", never silent
        alarm(f"orchestra-registry.db unreadable ({e!r}) — falling back to the flat "
              f"agent-sessions.json (readers-db-first)")
        return {}


def load_meta_db_first(orch, flat_meta, *, alarm=_default_alarm) -> dict:
    """Union the DB-derived meta DB-wins-per-key OVER ``flat_meta``.

    Never shrinks below ``flat_meta`` (flat supplies keys the DB lacks). Fail-loud
    alarm on: absent/empty DB (returns flat unchanged) and a materially-below-flat DB
    key count (torn/partial migration — union still safe). Per-key DB-wins skew is
    SILENT (the designed normal path). ``flat_meta`` is the router's usual
    agent-sessions.json dict; this function reads the DB but never the flat file.
    """
    unreadable: list = []
    db_meta = build_agent_meta_db(orch, alarm=unreadable.append)
    active = {"db_unreadable": unreadable[-1]} if unreadable else {}
    if not db_meta:
        # Both sides empty is a fresh install with no seats yet (`orchestra init` arms the
        # store with 0 seats): nothing disagrees, so nothing to report.
        if flat_meta and not unreadable:        # unreadable already says why it is empty
            active["db_empty"] = ("orchestra-registry.db absent/empty under cutover — resolving "
                                  "from the flat agent-sessions.json only (readers-db-first fail-safe)")
        _episodes(orch, active, alarm)
        return dict(flat_meta)

    if flat_meta and len(db_meta) < len(flat_meta) * _COUNT_ANOMALY_FLOOR:
        active["count_below_flat"] = (
            f"DB canonical/session count ({len(db_meta)}) is far below the flat "
            f"key count ({len(flat_meta)}) — possible torn/partial migration; the "
            f"union preserves flat keys but investigate (readers-db-first)")
    _episodes(orch, active, alarm)

    # Per-key DB-wins over a stale flat file is the DESIGNED NORMAL behavior of this
    # fix (the flat files flap on the shared tree; the DB is truth) — NOT an incident.
    # Firing a gm alert on every such skew floods the inbox on every resolve (gm gate
    # spot-fix 2026-09-04, steer #2). So per-key skew is SILENT; only the genuine
    # DB-integrity problems (absent/empty/torn — handled above) alert gm.
    merged = dict(flat_meta)
    for key, doc in db_meta.items():
        merged[key] = doc
    return merged


def registry_agent_db(orch, agent_id, *, alarm=_default_alarm):
    """Spawn-path DB-first (DEC-1788603298): resolve one agent's REGISTRY record from the
    identity DB, read-only, for spawn-agent.sh's config resolution when the flat
    registry.json has flapped the agent out. Returns the record dict or None.

    Built from the tested ``projector._build_faithful_registry`` (canonical members +
    provisional ``<root>-g<N>`` aliases + retired ``<root>-gen<N>`` archives), then:
    - **[C2]** EXCLUDE retired archives — a retired identity must never be spawnable (its
      doc may carry a stale tmux_session, or the synth archive carries none). Only
      canonical + provisional (``status != 'retired'``) records are returned.
    - **[C1]** guarantee a REAL cwd: a provisional alias's cwd is null; resolve
      alias/doc cwd → ``lineages[root].cwd`` → the root's live agent-doc cwd → ``orch``
      (the repo root, always an existing dir). spawn-agent.sh:369 rejects a nonexistent
      cwd and skips the auto-register default on this path.
    Never writes; **never raises** — an absent/unreadable DB or a corrupt payload row
    (the reused ``json.loads`` can raise) degrades to None (the caller's flat-miss).
    """
    dbp = _db_path(orch)
    if not os.path.exists(dbp):
        return None
    try:
        conn = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None
        try:
            from scripts.identity_store import projector
            # skip_bad: a concurrent writer can transiently leave a source_records row
            # with an empty/partial payload_json under a mode=ro read; skipping that one
            # row (rather than raising) keeps every GOOD agent — incl. the flapped
            # provisional alias — resolvable instead of fail-safe-to-flat (BUG3).
            snap = projector._read_faithful_snapshot(conn, skip_bad=True, warn=_transient_warn)
        finally:
            conn.close()
        reg = projector._build_faithful_registry(snap)
    except (sqlite3.Error, ValueError, json.JSONDecodeError, KeyError) as e:
        alarm(f"orchestra-registry.db unreadable/corrupt ({e!r}) — spawn resolve falls "
              f"back to the flat registry.json (readers-db-first)")
        return None

    rec = (reg.get("agents") or {}).get(agent_id)
    if rec is None:
        return None
    if str(rec.get("status", "")).lower() == "retired":  # [C2] never spawn a retired id
        return None
    if not rec.get("tmux_session"):
        # A degraded/incomplete record — e.g. a canonical member whose OWN agent doc was
        # a skipped bad-payload row, leaving only the {name,lineage_root} fallback — is NOT
        # spawnable. Return None so the caller falls through to flat / auto-register safely
        # rather than spawning an empty tmux session name.
        return None

    rec = dict(rec)
    cwd = rec.get("cwd")  # [C1] guarantee an existing dir
    if not cwd or not os.path.isdir(str(cwd)):
        root = rec.get("lineage_root") or agent_id
        lin = (snap.get("lineages") or {}).get(root, {})
        root_doc = (snap.get("docs", {}).get("agent") or {}).get(root)
        root_doc_cwd = root_doc[1].get("cwd") if root_doc else None
        for cand in (lin.get("cwd"), root_doc_cwd, str(orch)):
            if cand and os.path.isdir(str(cand)):
                rec["cwd"] = str(cand)
                break
        else:
            rec["cwd"] = str(orch)
    return rec
