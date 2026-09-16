#!/usr/bin/env python3
"""Atomically update state/agent-sessions.json with file locking.

Usage:
  python3 scripts/sessions-update.py <agent_id> [--field key=value ...]
  python3 scripts/sessions-update.py <agent_id> --json '{"succeeded_by": "cand-g2"}'

agent-sessions.json is a FLAT dict keyed by agent_id (NOT wrapped in an
"agents" key, unlike registry.json). This mirrors registry-update.py's
locking and atomic-write structure so lineage succession edges land in the
store that park-idle.py and message-router.py actually read.

All agents and scripts that modify agent-sessions.json should use this
instead of direct read-modify-write to prevent race conditions.
"""

import argparse
import fcntl
import json
import os
import sys

# Gap 4 (WS1 Stage-3): target the LIVE state store, not the script's own tree.
# Run from a worktree, os.path.dirname(__file__) points at the WORKTREE's
# state/agent-sessions.json, so a lineage wire-edge landed in the worktree copy
# and was invisible to the live resolver (this masked Gap 5). Honor ORCHESTRA_DIR
# (the same env message-router / executors already use); fall back to the script's
# own repo root only when it is unset.
_DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_SESSIONS = os.path.join(
    os.environ.get("ORCHESTRA_DIR", _DEFAULT_ROOT),
    "state",
    "agent-sessions.json",
)


def _cv4_observe_sessions_write(agent_id, on_disk, record):
    """CV4 W4 write-fence SHADOW routing (observe-only; NEVER alters the write).
    Off-switches live in write_fence (kill-file ~/runtime/CV4_WRITE_FENCE_DISABLED
    + MODE flag). observe_write RETURNS a status (1 logged / 0 failed / -1 disabled)
    — we check the RETURN, not the absence of an exception (the W7 dead-except
    trap). A 0 => one visible stderr line; the real write ALWAYS proceeds. Import
    and call are fully wrapped so a broken guard can never break sessions-update."""
    try:
        import importlib.util
        _wf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "continuity", "write_fence.py")
        _spec = importlib.util.spec_from_file_location("cv4_write_fence", _wf_path)
        _wf = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_wf)
        rc = _wf.observe_write(store="sessions", key=agent_id, record=record,
                               on_disk=on_disk, writer="sessions-update")
        if rc == 0:
            sys.stderr.write(
                "CV4 W4 observe error (rate-limited): shadow write failed for "
                "sessions/%s (real write proceeded)\n" % agent_id)
    except Exception as e:
        sys.stderr.write("CV4 W4 observe error (rate-limited): %r "
                         "(real write proceeded)\n" % e)


class CutoverRefused(Exception):
    """Under cutover the sessions CLI refused to write a session around the store for a
    root with no canonical generation. Fail-closed per DEC-1788346974 (b)."""


def _cutover_sessions_write(agent_id: str, fields: dict):
    """Under cutover route the session write to the transactional store instead of
    json.dump (the §B conversion). Returns the record dict when handled, or None when
    the cutover is inactive (caller does its BYTE-IDENTICAL legacy write). INERT-safe:
    cheap flag-FILE/env check FIRST; the store is imported ONLY on the armed path."""
    orch = os.environ.get("ORCHESTRA_DIR", _DEFAULT_ROOT)
    if not (os.path.exists(os.path.join(orch, "state", "identity-store-cutover.flag"))
            or os.environ.get("IDENTITY_STORE_CUTOVER") == "1"):
        return None
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from scripts.identity_store import identity_writer, orchestra_db
    dbp = os.path.join(orch, "state", "orchestra-registry.db")
    conn = orchestra_db.get_connection(dbp)
    try:
        has_canon = conn.execute(
            "SELECT 1 FROM canonical WHERE root=?", (agent_id,)).fetchone() is not None
        doc_row = conn.execute(
            "SELECT payload_json FROM source_records WHERE file='agent-sessions.json' "
            "AND kind='session' AND key=?", (agent_id,)).fetchone()
    finally:
        conn.close()
    if not has_canon:
        raise CutoverRefused(
            f"cutover active: no canonical generation for {agent_id!r} — refusing to "
            f"write a session around the store")
    base = json.loads(doc_row["payload_json"]) if doc_row else {}
    merged = {**base, **fields}
    identity_writer.update_session(orch, agent_id, fields, full_record=merged)
    return merged


def _project_now_best_effort(agent_id: str):
    """F3-chokepoint (gm msg_a2939cc3): after a successful DB write under cutover, force a
    SYNCHRONOUS projection so the next legacy reader sees the write before the daemon's
    debounce closes. Covers every CLI caller by construction. POSTURE: the DB write is
    durable+correct; a projection failure is SURFACED but NOT rolled back (the daemon
    catches up; a racing spawn hits U16 safely). Contrast rotate_agent (owns the spawn ->
    aborts before it)."""
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from scripts.identity_store import identity_writer
    try:
        identity_writer.project_now(os.environ.get("ORCHESTRA_DIR", _DEFAULT_ROOT))
    except Exception as e:
        sys.stderr.write(
            f"F3-chokepoint WARNING: synchronous projection failed after the sessions "
            f"DB write for {agent_id!r} ({e!r}). The DB write PERSISTS; the projector "
            f"daemon will refresh the copies.\n")


def update(agent_id: str, fields: dict, path: str = AGENT_SESSIONS) -> dict:
    """Atomically read-modify-write agent-sessions.json with file locking.

    agent-sessions.json is a flat dict keyed by agent_id. Merge `fields` into
    data[agent_id], creating the entry if missing, preserving all other
    fields, via an atomic temp-file replace under fcntl.LOCK_EX. Under cutover the
    write routes to the transactional store instead (no JSON drift), then force-projects
    synchronously (F3-chokepoint) so an immediate legacy reader sees it.
    """
    handled = _cutover_sessions_write(agent_id, fields)
    if handled is not None:
        _project_now_best_effort(agent_id)
        return handled
    with open(path, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            data = json.load(f)
            _cv4_on_disk = dict(data.get(agent_id) or {})  # CV4 W4: prior record
            if agent_id in data:
                data[agent_id].update(fields)
            else:
                data[agent_id] = dict(fields)
            _cv4_observe_sessions_write(agent_id, _cv4_on_disk, data[agent_id])  # CV4 W4
            tmp = path + ".tmp"
            with open(tmp, "w") as t:
                json.dump(data, t, indent=2)
                t.write("\n")
            os.replace(tmp, path)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return data[agent_id]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Atomically update state/agent-sessions.json"
    )
    parser.add_argument("agent_id", help="Agent ID to register/update")
    parser.add_argument("--field", action="append", default=[],
                        help="key=value fields to set")
    parser.add_argument("--json", dest="json_data",
                        help="Full JSON object to merge")
    args = parser.parse_args(argv)

    fields = {}
    if args.json_data:
        fields = json.loads(args.json_data)
    for field in args.field:
        key, _, value = field.partition("=")
        # Parse booleans and numbers
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        elif value.isdigit():
            value = int(value)
        fields[key] = value

    if not fields:
        # Just check if agent exists
        with open(AGENT_SESSIONS) as f:
            data = json.load(f)
        if args.agent_id in data:
            print(json.dumps(data[args.agent_id], indent=2))
        else:
            print(f"Agent '{args.agent_id}' not in agent-sessions",
                  file=sys.stderr)
            sys.exit(1)
        return

    try:
        update(args.agent_id, fields)
    except CutoverRefused as e:
        print(f"REFUSE (cutover): {e}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps({"updated": True, "agent_id": args.agent_id,
                      "fields": list(fields.keys())}))


if __name__ == "__main__":
    main()
