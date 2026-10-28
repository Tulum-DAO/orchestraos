"""Start a dead agent fresh, as its next generation: same id, same name, same memory and mailbox, at once.

Unlike a live rotation there is nothing to overlap with (the predecessor has stopped), so there is no temporary
name and no read-back gate: the successor takes the agent's own tmux session from the start, so mail and the
dashboard point at it immediately, and its pane is usable as soon as Claude's screen is up.

Low latency is the point:
- the predecessor's generation is marked retired, and the normal spawn (spawn-agent.sh -> spawn_adopt) mints the
  next generation and repoints the agent's identity to it;
- the spawn runs in the background; `start()` returns as soon as the session exists (a few seconds);
- the first prompt is short (FRESH_INIT_PROMPT_FILE): who it is, its role prompt, ONE line on what its
  predecessor was last doing (read from the end of its last session, no model), and that a briefing is coming;
- the full briefing is built and sent in the background (scripts/briefing.py), usually already built at death;
- until it loads, the successor's hold hook (hooks/briefing_hold.py, BRIEFING_HOLD_ROOT) holds its replies.
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
from scripts.identity_store.seat_names import base_id  # noqa: E402

SESSION_WAIT_S = 15


class NotStartable(ValueError):
    """The agent can't be started fresh (unknown, already running, a built-in mailbox), with the reason."""


def retire_current_generation(orchestra_dir, root, now=None) -> bool:
    """Mark the agent's current generation retired (its process is gone), so the spawn mints the next one
    instead of coming back under the old generation. Respects the identity store's freeze and cutover switch;
    False when there was nothing to retire (already retired, no identity DB, cutover off)."""
    from scripts.identity_store import cutover, freeze, orchestra_db
    freeze.barrier(str(orchestra_dir))
    if not cutover.is_active(str(orchestra_dir)):
        return False
    conn = orchestra_db.get_connection(os.path.join(str(orchestra_dir), "state", "orchestra-registry.db"))
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT generation_id FROM canonical WHERE root = ?", (root,)).fetchone()
        n = 0
        if row:
            n = conn.execute("UPDATE generations SET retired_at = ?, note = COALESCE(note, '') || ? "
                             "WHERE id = ? AND retired_at IS NULL",
                             (now or orchestra_db._utcnow(), " [retired for a fresh successor]", row[0])).rowcount
        conn.execute("COMMIT")
        return bool(n)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def first_prompt(root, orchestra_dir, name=None) -> str:
    rec = briefing.seat_record(root, orchestra_dir)
    label = f"{name} ({root})" if name and name != root else root
    role = rec.get("system_prompt") or f"prompts/{root}.md"
    role_path = role if os.path.isabs(role) else str(ROOT / role)
    mem = Path(orchestra_dir) / "memory" / root / "MEMORY.md"
    lines = [
        f"You are {label}, a fresh start of this agent: its next generation, with the same id, name, memory and "
        f"mailbox. Your predecessor has stopped; you take over its work.",
        f"Your role and standing instructions: read {role_path} now." if Path(role_path).exists() else "",
        briefing.last_line(root, orchestra_dir),
        "A full briefing on what your predecessor did is being prepared and will arrive shortly as a message. Until "
        "it does, don't guess at its work.",
        f"Your memory: {mem}." if mem.exists() else "",
        f"Your inbox: python3 {ROOT}/msg_store.py inbox --agent {root}",
    ]
    return "\n".join(x for x in lines if x)


def _pane_pid(tmux):
    r = subprocess.run(["tmux", "list-panes", "-t", f"{tmux}:", "-F", "#{pane_pid}"], capture_output=True, text=True)
    return r.stdout.split()[0] if r.returncode == 0 and r.stdout.split() else None


def _session_up(tmux, old_pane=None) -> bool:
    """The successor is up: its session has a NEW pane (not the one a crashed Claude left behind) with Claude
    running in it."""
    pid = _pane_pid(tmux)
    if not pid or pid == old_pane:
        return False
    try:
        from scripts.identity_store.seat_resolve import _pane_has_claude
        return bool(_pane_has_claude(tmux))
    except Exception:  # noqa: BLE001
        return True


def start(root, orchestra_dir=None, directory=None, spawn=None, deliver=None, wait_s=SESSION_WAIT_S,
          session_up=_session_up, home=None) -> dict:
    """Start `root` fresh. Returns {id, name, session_up, last_line} as soon as its session exists (or after
    wait_s, still starting). Raises NotStartable with a plain reason."""
    od = briefing.data_dir(orchestra_dir)
    root = base_id(root)
    if directory is None:
        from scripts.identity_store.seat_resolve import load_directory
        directory = load_directory(od)
    seat = next((s for s in directory if s["id"] == root), None)
    if not seat:
        raise NotStartable(f"there is no agent with the id {root!r}")
    if seat.get("builtin"):
        raise NotStartable(f"{root} is not an agent that can be started")
    if seat.get("alive"):
        raise NotStartable(f"{seat.get('name') or root} is already running")
    # the predecessor's last session, pinned now: the spawn soon records the successor's own session in its place
    pred = briefing.transcript_path(root, od, home) if not (briefing.bdir(od) / f"{root}.pending").exists() else None
    b = briefing.bdir(od)
    b.mkdir(parents=True, exist_ok=True)
    (b / f"{root}.pending").unlink(missing_ok=True)
    pred = pred or briefing.transcript_path(root, od, home)
    retire_current_generation(od, root)
    for stale in ("loaded", "timedout"):
        (b / f"{root}.{stale}").unlink(missing_ok=True)
    briefing._write(b / f"{root}.pending", json.dumps({
        "started_at": time.time(), "generation_of": root, "transcript": str(pred) if pred else None,
        # a briefing must be built after this to cover the predecessor's whole last session
        "source_mtime": os.path.getmtime(pred) if pred else None}))
    prompt_file = Path(f"/tmp/agent-fresh-{root}.md")
    prompt_file.write_text(first_prompt(root, od, seat.get("name")))
    rec = briefing.seat_record(root, od)
    tmux = rec.get("tmux_session") or root
    old_pane = _pane_pid(tmux) if session_up is _session_up else None
    env = {**os.environ, "ORCHESTRA_DIR": str(od), "FRESH_INIT_PROMPT_FILE": str(prompt_file),
           "BRIEFING_HOLD_ROOT": root, "REINCARNATION": "true"}
    if rec.get("runtime"):
        env["AGENT_RUNTIME"] = rec["runtime"]
    if spawn:
        spawn(root, env)
    else:
        subprocess.Popen(["bash", str(ROOT / "spawn-agent.sh"), root], cwd=str(ROOT), env=env,
                         stdout=open(b / f"{root}.spawn.log", "a"), stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    (deliver or briefing.build_and_deliver_detached)(root, od)
    deadline = time.monotonic() + wait_s
    up = session_up(tmux, old_pane) if session_up is _session_up else session_up(tmux)
    while not up and time.monotonic() < deadline:
        time.sleep(0.25)
        up = session_up(tmux, old_pane) if session_up is _session_up else session_up(tmux)
    return {"id": root, "name": seat.get("name") or root, "session_up": up, "last_line": briefing.last_line(root, od)}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Start a dead agent fresh as its next generation.")
    ap.add_argument("agent_id")
    a = ap.parse_args(argv)
    try:
        print(json.dumps(start(a.agent_id)))
    except NotStartable as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
