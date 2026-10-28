"""Bring a dead agent back: resume its last session, or start it fresh as its next generation.

- resume: reopens the agent's last Claude session, with everything it had in context, through the one sanctioned
  path (spawn-agent.sh <id> --resume <session id>: the identity gate gets the known session, the launch carries
  --resume, and a short wake line replaces the first prompt). Only when that session can still be resumed: the
  agent has a recorded session id and its session file still exists.
- fresh: scripts/fresh_successor.py (the next generation, a one-line "last doing", the briefing in the background).
- auto: resume when it can, otherwise fresh (the dashboard's single Resume button).

Either way it returns as soon as Claude is running in the agent's new pane (a few seconds); the spawn finishes
in the background.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import briefing  # noqa: E402
from scripts import fresh_successor as fs  # noqa: E402
from scripts.identity_store.seat_names import base_id  # noqa: E402

NotStartable = fs.NotStartable


def resumable_session(root, orchestra_dir=None, home=None):
    """The agent's last session id when that session can still be resumed (its file exists), else None."""
    rec = briefing.seat_record(base_id(root), orchestra_dir)
    sid = rec.get("session_id")
    return sid if sid and briefing.transcript_path(base_id(root), orchestra_dir, home) else None


def _last_model(orchestra_dir, root):
    """The model the agent's last generation ran on (identity DB, read-only), or None."""
    import sqlite3
    try:
        c = sqlite3.connect(f"file:{Path(orchestra_dir) / 'state' / 'orchestra-registry.db'}?mode=ro", uri=True, timeout=5)
        try:
            row = c.execute("SELECT model FROM generations WHERE root = ? ORDER BY generation DESC LIMIT 1", (root,)).fetchone()
            return row[0] if row and row[0] and row[0] != "unknown" else None
        finally:
            c.close()
    except Exception:  # noqa: BLE001
        return None


def _seat(root, orchestra_dir, directory):
    if directory is None:
        from scripts.identity_store.seat_resolve import load_directory
        directory = load_directory(briefing.data_dir(orchestra_dir))
    seat = next((s for s in directory if s["id"] == root), None)
    if not seat:
        raise NotStartable(f"there is no agent with the id {root!r}")
    if seat.get("builtin"):
        raise NotStartable(f"{root} is not an agent that can be started")
    if seat.get("alive"):
        raise NotStartable(f"{seat.get('name') or root} is already running")
    return seat


def resume(root, orchestra_dir=None, directory=None, spawn=None, session_up=fs._session_up, wait_s=fs.SESSION_WAIT_S,
           home=None) -> dict:
    """Reopen the agent's last session. Raises NotStartable when it is running, unknown, or has no session that
    can be resumed (then a fresh start is the way back)."""
    od = briefing.data_dir(orchestra_dir)
    root = base_id(root)
    seat = _seat(root, od, directory)
    sid = resumable_session(root, od, home)
    if not sid:
        raise NotStartable(f"{seat.get('name') or root} has no session that can be resumed; start it fresh instead")
    rec = briefing.seat_record(root, od)
    tmux = rec.get("tmux_session") or root
    old_pane = fs._pane_pid(tmux) if session_up is fs._session_up else None
    env = {**os.environ, "ORCHESTRA_DIR": str(od)}
    for k in ("FRESH_INIT_PROMPT_FILE", "BRIEFING_HOLD_ROOT"):     # a resume carries its own context: no hold
        env.pop(k, None)
    # a retired agent has no registry row: its working folder from the session file itself, its runtime and model
    # from its last generation, so the spawn registers it again where the session can be found
    env["AGENT_RUNTIME"] = rec.get("runtime") or "claude"
    if not rec.get("cwd"):
        cwd = briefing.session_cwd(briefing.transcript_path(root, od, home))
        if cwd:
            env["AGENT_CWD"] = cwd
    if not rec.get("model"):
        model = _last_model(od, root)
        if model:
            env["AGENT_MODEL"] = model
    if spawn:
        spawn(root, sid, env)
    else:
        log = briefing.bdir(od) / f"{root}.spawn.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["bash", str(ROOT / "spawn-agent.sh"), root, "--resume", sid], cwd=str(ROOT), env=env,
                         stdout=open(log, "a"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    deadline = time.monotonic() + wait_s
    check = (lambda: session_up(tmux, old_pane)) if session_up is fs._session_up else (lambda: session_up(tmux))
    up = check()
    while not up and time.monotonic() < deadline:
        time.sleep(0.25)
        up = check()
    return {"id": root, "name": seat.get("name") or root, "mode": "resume", "session_up": up}


def revive(root, mode="auto", orchestra_dir=None, **kw) -> dict:
    """resume | fresh | auto (resume when its last session can be resumed, else fresh)."""
    if mode not in ("auto", "resume", "fresh"):
        raise ValueError("mode must be auto, resume or fresh")
    od = briefing.data_dir(orchestra_dir)
    if mode == "auto":
        mode = "resume" if resumable_session(base_id(root), od, kw.get("home")) else "fresh"
    if mode == "resume":
        return resume(root, od, **kw)
    r = fs.start(root, orchestra_dir=od, **{k: v for k, v in kw.items() if k in ("directory", "session_up", "wait_s", "home")})
    return {**r, "mode": "fresh"}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Resume a dead agent's last session, or start it fresh.")
    ap.add_argument("agent_id")
    ap.add_argument("--mode", choices=["auto", "resume", "fresh"], default="auto")
    a = ap.parse_args(argv)
    try:
        print(json.dumps({"ok": True, **revive(a.agent_id, a.mode)}))
    except (NotStartable, ValueError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
