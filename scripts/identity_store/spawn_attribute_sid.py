#!/usr/bin/env python3
"""spawn_attribute_sid — post-boot sid attribution for PLAIN spawns (spawn-agent.sh).

Rotation/BG greens get their live sid captured by capture_green_sid.register_green_session;
a plain spawn had no such step, so a freshly (re)spawned seat sat with generations.session_id
NULL / no resume_command until the */15 reconciler happened to attribute it (the operator 'resume
apprvd-pm' 2026-09-16: hand-attributed after boot). This CLI reuses the SAME primitive with
the provider-agnostic resolver, writes DB-first (session take-over + registry doc keys) and
re-projects. Bounded poll; inert (exit 0, {"handled": false}) when the cutover is off.
Usage: spawn_attribute_sid.py <agent_id> [--attempts N] [--sleep S]"""
import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from scripts.identity_store import cutover, identity_writer  # noqa: E402


def resume_command_for(runtime, sid):
    """The runtime's own resume shape (the same strings the swap path finalizes)."""
    rt = (runtime or "claude").lower()
    if rt == "claude":
        return f"claude --resume {sid} --dangerously-skip-permissions"
    if rt == "codex":
        return f"codex --yolo resume {sid}"
    return None


def attribute(orch, agent_id, *, resolve_cid_fn, runtime=None, attempts=8, sleep_s=3.0,
              sleep_fn=None):
    """Returns {"handled": bool, "sid": str|None}. DB-first: generations.session_id via the
    session take-over (update_session full_record), registry doc session_id/resume_command,
    then project_now. Never raises to the caller."""
    if not cutover.is_active(orch):
        return {"handled": False, "sid": None}
    from lineage_daemon.wal.capture_green_sid import register_green_session
    state = {"sid": None}

    def _update_session(aid, fields, full_record=None):
        sid = fields.get("session_id")
        state["sid"] = sid
        resume = resume_command_for(runtime, sid)
        f = dict(fields)
        if resume:
            f["resume_command"] = resume
        rec = dict(full_record or {})
        rec.update(f)
        rec.setdefault("name", aid)
        rec.setdefault("lineage_root", aid)
        rec.setdefault("tmux_session", aid)
        rec.setdefault("status", "online")
        identity_writer.update_session(orch, aid, f, full_record=rec)
        # registry doc keys too (M1: the projector only corrects keys PRESENT in the doc)
        try:
            import json as _json
            reg = _json.load(open(os.path.join(orch, "registry.json"))).get("agents", {})
            doc = dict(reg.get(aid) or {"name": aid, "lineage_root": aid})
            doc.update({k: v for k, v in f.items()})
            identity_writer.update_registry_agent(orch, aid, {k: v for k, v in f.items()},
                                                  full_record=doc)
        except Exception:  # noqa: BLE001 — the session write above is the load-bearing one
            pass

    try:
        sid = register_green_session(
            orch, agent_id, resolve_cid_fn=resolve_cid_fn,
            update_session_fn=_update_session,
            project_fn=lambda: identity_writer.project_now(orch),
            poll_attempts=attempts, sleep_fn=sleep_fn or (lambda: time.sleep(sleep_s)))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"spawn_attribute_sid: {agent_id}: {e}\n")
        sid = None
    return {"handled": True, "sid": sid or state["sid"]}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("agent_id")
    p.add_argument("--runtime", default=None)
    p.add_argument("--attempts", type=int, default=8)
    p.add_argument("--sleep", type=float, default=3.0)
    a = p.parse_args(argv)
    orch = os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))
    from lineage_daemon.wal.ctx_adapters import resolve_cid_any
    out = attribute(orch, a.agent_id, resolve_cid_fn=resolve_cid_any, runtime=a.runtime,
                    attempts=a.attempts, sleep_s=a.sleep)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
