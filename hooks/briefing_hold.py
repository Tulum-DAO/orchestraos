#!/usr/bin/env python3
"""UserPromptSubmit hook for a fresh successor: hold the reply until its predecessor's briefing has loaded.

Installed only in a fresh successor's own settings file (scripts/spawn_permission_rules.py, when the spawn sets
BRIEFING_HOLD_ROOT), never globally. Typing is never blocked: the text is submitted as usual and this hook runs
before the model answers it.

- The briefing already reached this successor (<id>.loaded): returns at once, adds nothing.
- The briefing is there: attaches it as context to this prompt and marks it loaded, so the answer is given
  knowing what the predecessor did.
- Not there yet: waits for it (up to BRIEFING_HOLD_SECONDS, default 40), then attaches it.
- The spawn's own boot prompt ("You are <id>. Read ...") is never held: it gets the briefing if it is there.
- Still not there: lets the answer go ahead with a note to say plainly that the predecessor's context is
  missing (never hang, never guess), and remembers that it gave up, so later prompts only check, not wait.

Never fails the prompt: any error exits 0 with nothing added.
"""
import json
import os
import sys
import time
from pathlib import Path

HOLD_S = float(os.environ.get("BRIEFING_HOLD_SECONDS", "40"))
POLL_S = 0.25


def _ctx(text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}))


def run(root, orchestra_dir, hold_s=None, sleep=time.sleep, clock=time.monotonic, prompt=""):
    b = Path(orchestra_dir) / "state" / "briefings"
    md, loaded, gaveup, pending = (b / f"{root}.md", b / f"{root}.loaded", b / f"{root}.timedout", b / f"{root}.pending")
    hold_s = HOLD_S if hold_s is None else hold_s
    try:
        p = json.loads(pending.read_text())
        started, covers = p.get("started_at", 0), p.get("source_mtime") or 0
    except (OSError, ValueError):
        return None                                      # no fresh successor is waiting for a briefing: never hold

    def ready():
        """A briefing that covers ALL of the predecessor's last session: built after that session was last
        written (an older briefing, from before its latest work, is not ready)."""
        try:
            built = json.loads((b / f"{root}.json").read_text()).get("built_at", 0)
            return bool(built) and built >= covers and md.stat().st_size > 0
        except (OSError, ValueError):
            return False

    if loaded.exists() and loaded.stat().st_mtime >= started:
        return None                                      # already in this successor's context
    boot = str(prompt or "").lstrip().startswith(f"You are {root}. Read ")
    wait = 0 if boot or (gaveup.exists() and gaveup.stat().st_mtime >= started) else hold_s
    deadline = clock() + wait
    while not ready() and clock() < deadline:
        sleep(POLL_S)
    if ready():
        loaded.write_text(str(time.time()))
        return ("BRIEFING FROM YOUR PREDECESSOR (you are its successor: the next generation of this agent, same id, "
                "same name, same memory). Read it before answering; take up its work where it left off.\n\n"
                + md.read_text()[:20000])
    if boot:
        return None                                      # the boot prompt goes ahead; the hold starts with real work
    if wait:
        gaveup.write_text(str(time.time()))
        return ("Your predecessor's briefing has not arrived yet (waited %d seconds). Answer now, but say plainly that "
                "you don't have your predecessor's context yet, and don't guess what it was doing; the briefing will "
                "arrive shortly as a message." % int(wait))
    return ("Your predecessor's briefing still hasn't arrived. If this needs its context, say so plainly instead of "
            "guessing.")


def main():
    try:
        prompt = json.loads(sys.stdin.read() or "{}").get("prompt", "")
    except Exception:  # noqa: BLE001
        prompt = ""
    root = os.environ.get("BRIEFING_HOLD_ROOT", "").strip()
    od = os.environ.get("ORCHESTRA_DIR", "").strip()
    if not root or not od:
        return 0
    try:
        text = run(root, od, prompt=prompt)
    except Exception:  # noqa: BLE001 - never fail a prompt
        return 0
    if text:
        _ctx(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
