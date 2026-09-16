#!/usr/bin/env python3
"""Atomically update registry.json with file locking.

Usage:
  python3 scripts/registry-update.py <agent_id> [--field key=value ...]
  python3 scripts/registry-update.py meetings --field tier=T2 --field machine=vps

All agents and scripts that modify registry.json should use this
instead of direct read-modify-write to prevent race conditions.
"""

import argparse
import fcntl
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from runtime_signatures import (  # noqa: E402
    RuntimeResolutionError, validate_runtime_for_registration)

# REGISTRY_PATH env override (mirrors spawn-agent.sh's REGISTRY + the
# runtime_signatures ORCHESTRA_DIR pattern) so the registration invariant can be
# exercised against a SCRATCH registry without touching the live file. Production
# sets no REGISTRY_PATH env and writes the checkout's registry.json exactly as before.
REGISTRY = os.environ.get(
    "REGISTRY_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "registry.json"))


def _cv4_observe_registry_write(agent_id, on_disk, record):
    """CV4 W5 write-fence SHADOW routing (observe-only; NEVER alters the write).
    Off-switches live in write_fence (kill-file ~/runtime/CV4_WRITE_FENCE_DISABLED
    + MODE flag). observe_write RETURNS a status (1 logged / 0 failed / -1 disabled)
    — we check the RETURN, not the absence of an exception (the W7 dead-except
    trap). A 0 => one visible stderr line; the real write ALWAYS proceeds. Import
    and call are fully wrapped so a broken guard can never break registry-update."""
    try:
        import importlib.util
        _wf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "continuity", "write_fence.py")
        _spec = importlib.util.spec_from_file_location("cv4_write_fence", _wf_path)
        _wf = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_wf)
        rc = _wf.observe_write(store="registry", key=agent_id, record=record,
                               on_disk=on_disk, writer="registry-update")
        if rc == 0:
            sys.stderr.write(
                "CV4 W5 observe error (rate-limited): shadow write failed for "
                "registry/%s (real write proceeded)\n" % agent_id)
    except Exception as e:
        sys.stderr.write("CV4 W5 observe error (rate-limited): %r "
                         "(real write proceeded)\n" % e)


class CutoverRefused(Exception):
    """Under cutover the generic registry CLI refused to ESTABLISH an unknown/partial
    identity around the store (no lineage row; not a successor of an existing lineage).
    Fail-closed per DEC-1788346974 (b): use the sanctioned adopt path (spinup U16) or
    the rotate/executors provisional seam."""


def _cutover_registry_write(agent_id: str, data: dict):
    """Under cutover route the registry write to the transactional store instead of
    json.dump (the §B conversion — the CLI stays the surface, the body writes the DB).
    Returns the record dict when handled, or None when the cutover is inactive (caller
    does its BYTE-IDENTICAL legacy write). INERT-safe: cheap flag-FILE/env check FIRST;
    the store is imported ONLY on the armed path."""
    orch = os.environ.get("ORCHESTRA_DIR", os.path.dirname(REGISTRY))
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
            "SELECT payload_json FROM source_records WHERE file='registry.json' "
            "AND kind='agent' AND key=?", (agent_id,)).fetchone()
        lr = data.get("lineage_root")
        gen = data.get("generation")
        has_lineage = bool(lr) and conn.execute(
            "SELECT 1 FROM lineages WHERE root=?", (lr,)).fetchone() is not None
    finally:
        conn.close()
    if has_canon:
        # existing canonical agent: config/status update + the DP-A2 full document.
        base = json.loads(doc_row["payload_json"]) if doc_row else {}
        merged = {**base, **data}
        identity_writer.update_registry_agent(orch, agent_id, data, full_record=merged)
        return merged
    gen_is_int = isinstance(gen, int) and not isinstance(gen, bool)
    if has_lineage and gen_is_int:
        # a successor of an EXISTING lineage with an INTEGER generation: register a
        # PROVISIONAL (non-canonical) generation; project_faithful synthesizes the
        # <root>-g<N> alias. NOT a canonical adopt (the -gN is provisional until
        # promoted — cf. p3b/rotate). A phantom lineage_root (no live lineage row) or
        # a non-integer generation FALLS THROUGH to fail-closed below — never mint a
        # provisional against a phantom lineage or a bogus generation.
        identity_writer.register_provisional(orch, lr, gen, model=data.get("model"))
        return dict(data)
    raise CutoverRefused(
        f"cutover active: refusing to establish identity for {agent_id!r} via the "
        f"generic registry CLI (no live lineage row / non-integer generation; not a "
        f"successor). Use spinup U16 adopt or the rotate/executors provisional seam.")


def _project_now_best_effort(agent_id: str):
    """F3-chokepoint (gm msg_a2939cc3): after a successful DB write under cutover, force a
    SYNCHRONOUS projection so the very next LEGACY registry.json reader (executors.py
    plan_spawn, spinup's tmux spawn, any CLI caller) sees the write immediately — before
    the projector daemon's debounce window closes. This ONE call in the shim covers every
    current/future CLI writer by construction (the §B-CLI-gap lesson).

    POSTURE (shim != rotate_agent): the DB write already committed and is durable+correct.
    A projection failure is SURFACED LOUDLY but does NOT roll back the write — the daemon's
    debounced pass refreshes it, and a caller that spawns before then hits U16 (b) which
    refuses safely (no partial mint). Rolling back a durable identity write would be worse.
    Contrast rotate_agent, which OWNS the spawn and aborts before it on a project_now raise."""
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from scripts.identity_store import identity_writer
    try:
        identity_writer.project_now(
            os.environ.get("ORCHESTRA_DIR", os.path.dirname(REGISTRY)))
    except Exception as e:
        sys.stderr.write(
            f"F3-chokepoint WARNING: synchronous projection failed after the registry "
            f"DB write for {agent_id!r} ({e!r}). The DB write PERSISTS (durable); the "
            f"projector daemon will refresh the read-only copies. An immediate spawn "
            f"reader may hit the debounce window until then (U16 refuses safely).\n")


def safe_update_registry(agent_id: str, data: dict) -> dict:
    """Atomically read-modify-write registry.json with file locking. Under cutover the
    write routes to the transactional store instead (no JSON drift), then force-projects
    synchronously (F3-chokepoint) so an immediate legacy reader sees it."""
    handled = _cutover_registry_write(agent_id, data)
    if handled is not None:
        _project_now_best_effort(agent_id)
        return handled
    with open(REGISTRY, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            reg = json.load(f)
            agents = reg.setdefault("agents", {})
            _cv4_on_disk = dict(agents.get(agent_id) or {})  # CV4 W5: prior record
            if agent_id in agents:
                agents[agent_id].update(data)
            else:
                # R4 registration invariant (spec §4.1 R4(a)): a NEW row MUST carry
                # a resolvable runtime so the zero-signal class cannot regrow. This
                # is CREATION-only — updates to an existing row (which already has a
                # runtime) are never gated. Validation runs under the lock, before
                # the write, so a refused registration writes NOTHING.
                validate_runtime_for_registration(data, agent_id=agent_id)
                agents[agent_id] = data
            _cv4_observe_registry_write(agent_id, _cv4_on_disk, agents[agent_id])  # CV4 W5
            f.seek(0)
            json.dump(reg, f, indent=2)
            f.write("\n")
            f.truncate()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return agents[agent_id]


def main():
    parser = argparse.ArgumentParser(description="Atomically update registry.json")
    parser.add_argument("agent_id", help="Agent ID to register/update")
    parser.add_argument("--field", action="append", default=[], help="key=value fields to set")
    parser.add_argument("--json", dest="json_data", help="Full JSON object to merge")
    args = parser.parse_args()

    data = {}
    if args.json_data:
        data = json.loads(args.json_data)
    for field in args.field:
        key, _, value = field.partition("=")
        # Parse booleans and numbers
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        elif value.isdigit():
            value = int(value)
        data[key] = value

    if not data:
        # Just check if agent exists
        with open(REGISTRY) as f:
            reg = json.load(f)
        if args.agent_id in reg.get("agents", {}):
            print(json.dumps(reg["agents"][args.agent_id], indent=2))
        else:
            print(f"Agent '{args.agent_id}' not in registry", file=sys.stderr)
            sys.exit(1)
        return

    try:
        result = safe_update_registry(args.agent_id, data)
    except RuntimeResolutionError as e:
        print(f"REFUSE: cannot register {args.agent_id!r} without a valid runtime "
              f"— {e}", file=sys.stderr)
        sys.exit(2)
    except CutoverRefused as e:
        print(f"REFUSE (cutover): {e}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps({"registered": True, "agent_id": args.agent_id, "fields": list(data.keys())}))


if __name__ == "__main__":
    main()
