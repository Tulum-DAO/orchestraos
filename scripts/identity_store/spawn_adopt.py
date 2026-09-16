#!/usr/bin/env python3
"""spawn_adopt.py — the fail-closed identity gate for spawn-agent.sh under the
identity-store cutover (gm msg_00bc6d2a, spawn-under-cutover registration class).

Why: under cutover the generic registry CLI refuses to ESTABLISH a new identity
(by design), and the sanctioned U16 adopt seam was only wired into the OB-specific
spinup script — so the general spawn path had NO way to seat a new agent, and raw
tmux+claude spawns (zero identity rows -> orphan/dual-chip, dead-lettered mail)
grew around it. This CLI wires the U16 seam into the general path: a spawn that
cannot register (lineage+generation+canonical+source_record) AND verify AND
project must NOT come up as a live seat.

Exit-code contract (consumed by spawn-agent.sh; exit 3 == its refusal code):
  0  {"handled": false}                cutover inactive -> caller's legacy path
  0  {"handled": true, ...}            identity registered+verified+projected,
                                       or already canonical ({"existing": true})
  3  (stderr says what was missing)    REFUSED -> caller must NOT tmux-spawn

Posture: this path OWNS the spawn (rotate_agent posture, NOT the shim posture) —
a projection failure aborts BEFORE the seat comes up, because an immediate legacy
reader (the spawn itself re-reads the flat registry) must see the row.
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.identity_store import cutover, identity_writer, orchestra_db  # noqa: E402

EXIT_REFUSED = 3


def _default_system_prompt(orch, agent_id):
    """prompts/<id>.md when it exists (the operator 'resume apprvd-pm': a blank system_prompt booted the
    seat on the foundation prompt only), else '' — spawn-agent.sh resolves the same default."""
    rel = os.path.join("prompts", f"{agent_id}.md")
    return rel if os.path.isfile(os.path.join(orch, rel)) else ""


def _orchestra_dir():
    return os.environ.get("ORCHESTRA_DIR",
                          os.path.expanduser("~/scripts/agent-orchestra"))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("agent_id")
    p.add_argument("--runtime", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--tier", default=None)
    p.add_argument("--cwd", default=None)
    p.add_argument("--machine", default="vps")
    p.add_argument("--generation", type=int, default=1)
    p.add_argument("--session-id", default=None)
    p.add_argument("--adaptive", action="store_true", default=False,
                   help="Adaptively resolve runtime/model via Quota Oracle if unprovided or exhausted")
    args = p.parse_args(argv)
    orch = _orchestra_dir()

    if args.adaptive or (not args.runtime and not args.model):
        try:
            import importlib.util
            q_spec = importlib.util.spec_from_file_location(
                "quota_oracle", os.path.join(orch, "scripts", "quota_oracle.py"))
            q_mod = importlib.util.module_from_spec(q_spec)
            q_spec.loader.exec_module(q_mod)
            res_rt, res_md, _ = q_mod.resolve_adaptive_runtime(args.runtime, args.model, args.tier or "T2")
            args.runtime = args.runtime or res_rt
            args.model = args.model or res_md
            args.tier = args.tier or "T2"
        except Exception as e:
            sys.stderr.write(f"spawn_adopt: adaptive quota resolution notice: {e}\n")

    if not cutover.is_active(orch):
        print(json.dumps({"handled": False}))
        return 0

    dbp = os.path.join(orch, "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)  # idempotent: schema-if-missing (fresh/scratch dirs)
    conn = orchestra_db.get_connection(dbp)
    try:
        has_canon = conn.execute(
            "SELECT 1 FROM canonical WHERE root=?",
            (args.agent_id,)).fetchone() is not None
    finally:
        conn.close()
    if has_canon:
        # Already a registered canonical seat. If its canonical generation is RETIRED
        # (reconciler: no process / no resume), this is a RESPAWN: mint the next generation
        # and repoint canonical in the DB BEFORE the flat write (gm msg_f145ea51: task-gm
        # came up flat-online behind a retired gen 1, guards_red every tick). A live
        # canonical needs nothing (its config updates flow through the shim CLI).
        conn = orchestra_db.get_connection(dbp)
        try:
            crow = conn.execute(
                "SELECT g.generation, g.retired_at, g.model FROM canonical cn "
                "JOIN generations g ON g.id = cn.generation_id WHERE cn.root=?",
                (args.agent_id,)).fetchone()
        finally:
            conn.close()
        if crow is None or crow["retired_at"] is None:
            print(json.dumps({"handled": True, "existing": True, "verified": True}))
            return 0
        model = args.model or (crow["model"] if str(crow["model"]).lower() != "unknown" else None)
        if not model:
            sys.stderr.write(
                f"spawn_adopt REFUSED for {args.agent_id!r}: canonical generation "
                f"{crow['generation']} is RETIRED and no model is known to mint the next one — "
                f"set AGENT_MODEL (a respawn must never seat behind a retired generation).\n")
            return EXIT_REFUSED
        try:
            new_gen = identity_writer.respawn_retired_canonical(orch, args.agent_id, model,
                                                                session_id=args.session_id)
        except Exception as e:  # noqa: BLE001 -- fail-closed: no DB row, no seat
            sys.stderr.write(f"spawn_adopt REFUSED for {args.agent_id!r}: respawn mint failed ({e})\n")
            return EXIT_REFUSED
        if new_gen is None:
            print(json.dumps({"handled": True, "existing": True, "verified": True}))
            return 0
        full = {"name": args.agent_id, "tier": args.tier or "T2", "machine": args.machine,
                "runtime": args.runtime, "model": model,
                "cwd": args.cwd or orch, "tmux_session": args.agent_id,
                "generation": new_gen, "lineage_root": args.agent_id,
                "always_on": False, "status": "online",
                "spawn_respawned_at": orchestra_db._utcnow()}
        if args.session_id:
            full["session_id"] = args.session_id          # resume of a KNOWN sid (roster)
        try:
            identity_writer.update_registry_agent(orch, args.agent_id, full, full_record=full)
            projected = identity_writer.project_now(orch)
        except Exception as e:  # noqa: BLE001 -- own-the-spawn posture: abort pre-seat
            sys.stderr.write(
                f"spawn_adopt REFUSED for {args.agent_id!r}: generation {new_gen} minted but "
                f"doc/projection FAILED ({type(e).__name__}: {e}); not seating.\n")
            return EXIT_REFUSED
        conn = orchestra_db.get_connection(dbp)
        try:
            ver = conn.execute(
                "SELECT g.generation, g.retired_at FROM canonical cn JOIN generations g "
                "ON g.id = cn.generation_id WHERE cn.root=?", (args.agent_id,)).fetchone()
        finally:
            conn.close()
        if ver is None or ver["retired_at"] is not None or ver["generation"] != new_gen:
            sys.stderr.write(f"spawn_adopt REFUSED for {args.agent_id!r}: post-respawn verify failed\n")
            return EXIT_REFUSED
        print(json.dumps({"handled": True, "existing": True, "respawned": True, "verified": True,
                          "generation": new_gen, "projected": bool(projected)}))
        return 0

    # NEW identity: must be COMPLETE — never fabricate a partial row (the
    # gutted-row / minimal-row class blocks every later rotation).
    # RE-ESTABLISH (the operator 2026-09-16 'resume apprvd-pm'): a lineage with generations but NO
    # canonical pointer (park-idle / reconciler full-retire) must mint max(generation)+1 —
    # adopting the default generation 1 would reuse the RETIRED row and point canonical at it.
    conn = orchestra_db.get_connection(dbp)
    try:
        prev_max = conn.execute("SELECT MAX(generation) FROM generations WHERE root=?",
                                (args.agent_id,)).fetchone()[0]
    finally:
        conn.close()
    reestablished = False
    if prev_max and args.generation <= prev_max:
        args.generation = prev_max + 1
        reestablished = True
    ident = {"root": args.agent_id, "generation": args.generation,
             "model": args.model, "tier": args.tier, "runtime": args.runtime,
             "machine": args.machine, "session_id": args.session_id}
    missing = [k for k in ("runtime", "model", "tier") if not ident.get(k)]
    if missing:
        sys.stderr.write(
            f"spawn_adopt REFUSED for {args.agent_id!r}: incomplete identity — "
            f"missing {missing}. Set AGENT_RUNTIME/AGENT_MODEL (and tier) so the "
            f"seat registers completely; refusing to mint a partial row.\n")
        return EXIT_REFUSED

    try:
        handled = identity_writer.adopt_identity(orch, ident)
    except orchestra_db.IdentityAdoptionError as e:
        sys.stderr.write(f"spawn_adopt REFUSED for {args.agent_id!r}: {e}\n")
        return EXIT_REFUSED
    if not handled:
        # cutover raced OFF between our check and the write: legacy path owns it.
        print(json.dumps({"handled": False}))
        return 0

    # Full source_record doc so the faithful projector serves a COMPLETE flat row
    # (the arturo-restore-dev gutted-payload lesson: a 6-field doc blocks
    # promote_successor forever).
    full = {"name": args.agent_id, "tier": args.tier, "machine": args.machine,
            "runtime": args.runtime, "model": args.model,
            "cwd": args.cwd or orch, "tmux_session": args.agent_id,
            "generation": args.generation, "lineage_root": args.agent_id,
            "always_on": False, "status": "online",
            "system_prompt": _default_system_prompt(orch, args.agent_id),
            "spawn_adopted_at": orchestra_db._utcnow()}
    if args.session_id:
        full["session_id"] = args.session_id
    try:
        identity_writer.update_registry_agent(orch, args.agent_id, full,
                                              full_record=full)
        projected = identity_writer.project_now(orch)
    except Exception as e:  # noqa: BLE001 -- own-the-spawn posture: abort pre-seat
        sys.stderr.write(
            f"spawn_adopt REFUSED for {args.agent_id!r}: registered in the DB but "
            f"doc/projection FAILED ({type(e).__name__}: {e}) — the immediate "
            f"spawn reader would miss the row; not seating. The DB rows persist; "
            f"re-run after fixing projection.\n")
        return EXIT_REFUSED

    # VERIFY BY EFFECT: re-read the store — the row must actually be there.
    conn = orchestra_db.get_connection(dbp)
    try:
        ver = conn.execute(
            "SELECT cn.root, g.generation FROM canonical cn "
            "JOIN generations g ON g.id = cn.generation_id WHERE cn.root=?",
            (args.agent_id,)).fetchone()
    finally:
        conn.close()
    if ver is None:
        sys.stderr.write(
            f"spawn_adopt REFUSED for {args.agent_id!r}: post-adopt verify "
            f"re-read found no canonical row — not seating.\n")
        return EXIT_REFUSED

    print(json.dumps({"handled": True, "existing": False, "verified": True,
                      "generation": ver["generation"], "reestablished": reestablished,
                      "projected": bool(projected)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
