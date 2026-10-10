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

# The ONE data-dir default is orchestra_cli.settings.data_dir (data-dir sweep S5); orchestra_cli
# lives in this file's checkout, appended (never prepended) so nothing already on the path is shadowed.
if os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) not in sys.path:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from orchestra_cli.settings import data_dir as _data_dir  # noqa: E402


EXIT_REFUSED = 3


def _default_system_prompt(orch, agent_id):
    """prompts/<id>.md (CODE: checked in the checkout, as spawn-agent.sh and seats.py read it) when it exists (the operator 'resume apprvd-pm': a blank system_prompt booted the
    seat on the foundation prompt only), else '' — spawn-agent.sh resolves the same default."""
    rel = os.path.join("prompts", f"{agent_id}.md")
    code_root = os.environ.get("ORCHESTRA_ROOT") or _ROOT
    return rel if os.path.isfile(os.path.join(code_root, rel)) else ""


class AdaptiveUnavailable(Exception):
    """The quota oracle could not load or answer: the spawn is refused, never silently skipped."""


def _spawn_log(orch, line):
    """One plain line to stderr AND the data dir's spawn log (logs/spawn-adopt.log): spawn-agent.sh
    discards this script's stdout and a daemon-driven spawn has no terminal, so stderr alone is lost."""
    sys.stderr.write(line + "\n")
    try:
        os.makedirs(os.path.join(orch, "logs"), exist_ok=True)
        with open(os.path.join(orch, "logs", "spawn-adopt.log"), "a") as f:
            f.write(f"{orchestra_db._utcnow()} {line}\n")
    except OSError:
        pass                      # the stderr line already went out


def _adaptive_pick(args):
    """Ask the quota oracle (CODE: the checkout's scripts/quota_oracle.py; its quota state is DATA, read
    from ORCHESTRA_DIR) for a runtime/model. Fills only what is MISSING. Raises AdaptiveUnavailable."""
    import importlib.util
    path = os.path.join(os.environ.get("ORCHESTRA_ROOT") or _ROOT, "scripts", "quota_oracle.py")
    try:
        spec = importlib.util.spec_from_file_location("quota_oracle", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"no loader for {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except (ImportError, OSError, SyntaxError) as e:
        raise AdaptiveUnavailable(f"the quota oracle could not load ({type(e).__name__}: {e})") from e
    try:
        rt, md, meta = mod.resolve_adaptive_runtime(args.runtime, args.model, args.tier or "T2")
    except Exception as e:  # noqa: BLE001 -- named below, and the spawn is refused
        raise AdaptiveUnavailable(f"the quota oracle failed ({type(e).__name__}: {e})") from e
    if not rt or not md:
        raise AdaptiveUnavailable(f"the quota oracle returned no runtime/model ({rt!r}, {md!r})")
    return rt, md, meta or {}


def _orchestra_dir():
    return os.environ.get("ORCHESTRA_DIR",
                          str(_data_dir()))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("agent_id")
    p.add_argument("--runtime", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--tier", default=None)
    p.add_argument("--reports-to", default=None,
                   help="parent seat (spawn-agent.sh passes the tier rule's reports_to). Blank = none; "
                        "lands in lineages.reports_to")
    p.add_argument("--role", default=None,
                   help="pm | worker (scripts/tier_rule.py). Kept in the agent document")
    p.add_argument("--cwd", default=None)
    p.add_argument("--machine", default="vps")
    p.add_argument("--generation", type=int, default=1)
    p.add_argument("--session-id", default=None)
    p.add_argument("--adaptive", action="store_true", default=False,
                   help="Adaptively resolve runtime/model via Quota Oracle if unprovided or exhausted")
    args = p.parse_args(argv)
    orch = _orchestra_dir()
    # Adaptive selection (quota oracle) runs ONLY on a FIRST spawn, further below: a respawn or
    # re-establish of a seat whose model is unknown keeps REFUSING (gm msg_5f2169d8: never silently
    # swap a seat's model). It used to run here for every call, from <data>/scripts/quota_oracle.py,
    # which never exists on a real install, and its broad except hid that: it never ran at all.

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
    if not prev_max and (args.adaptive or (not args.runtime and not args.model)):
        # FIRST spawn of this name: the quota oracle fills a MISSING runtime/model (an explicit
        # --adaptive asks it even when one was given; it keeps a healthy requested runtime).
        try:
            rt, md, meta = _adaptive_pick(args)
        except AdaptiveUnavailable as e:
            _spawn_log(orch, f"spawn_adopt REFUSED for {args.agent_id!r}: adaptive model selection is "
                             f"unavailable: {e}. Set AGENT_RUNTIME and AGENT_MODEL.")
            return EXIT_REFUSED
        if args.adaptive:
            # the oracle keeps a healthy requested runtime (and the requested model with it); when it
            # moves the seat to another runtime the model must move too, never a cross-runtime pair
            args.runtime, args.model = rt, md
        else:
            args.runtime, args.model = args.runtime or rt, args.model or md
        args.tier = args.tier or "T2"
        _spawn_log(orch, f"spawn_adopt: adaptive model selection picked runtime={args.runtime} "
                         f"model={args.model} for {args.agent_id!r} "
                         f"(account={meta.get('account')}, adapted={bool(meta.get('adapted'))})")
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
    # Hierarchy (DEC-1791574633518521): the parent the tier rule chose, and what the seat does.
    parent = (args.reports_to or "").strip()
    if parent and parent != args.agent_id:
        full["reports_to"] = parent
    if (args.role or "").strip():
        full["role"] = args.role.strip()
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
