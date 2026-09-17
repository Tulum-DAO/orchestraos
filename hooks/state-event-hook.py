#!/usr/bin/env python3
"""state-event-hook.py — Claude Code hook: push-based agent-state ground truth.

Installed by `orchestra init` into ~/.claude/settings.json for UserPromptSubmit, PreToolUse,
PostToolUse, Stop, SessionStart, SessionEnd and Notification. Writes one small
JSON per tmux pane to state/agent-events/panes/<pane>.json — truth the TUI
cannot lie about (see docs/agent-state-truth-audit.md, Tier 0). Consumed by
scripts/agent-status.py.

HARD CONTRACT: always exit 0, never print to stdout (UserPromptSubmit stdout is
injected as context; PreToolUse/Stop exit 2 would block the agent). Fail silent.
"""
import json
import os
import sys
import tempfile
import time

_DATA = os.environ.get("ORCHESTRA_DIR") or os.environ.get("ORCH_DIR") or os.path.expanduser("~/orchestra")
EVENTS_DIR = os.environ.get("ORCH_EVENTS_DIR", os.path.join(_DATA, "state", "agent-events", "panes"))

STATE_MAP = {
    "UserPromptSubmit": "working",
    "PreToolUse": "working",
    "PostToolUse": "working",
    "PreCompact": "working",
    "Stop": "idle",
    "SessionStart": "idle",
    "SessionEnd": "stopped",
}


def main() -> None:
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    event = data.get("hook_event_name") or ""

    if event == "Notification":
        msg = (data.get("message") or "").lower()
        # ONLY permission/approval notifications imply a blocking dialog.
        # "Claude is waiting for your input" fires on plain idle (verified live
        # 2026-08-09 — mapping it to waiting_permission false-flagged an idle
        # session); Stop already covers idle, so ignore everything else.
        if "permission" in msg or "approv" in msg:
            state = "waiting_permission"
        else:
            return
    else:
        state = STATE_MAP.get(event)
        if not state:
            return

    out = {
        "pane": pane,
        "session_id": data.get("session_id"),
        "cwd": data.get("cwd"),
        "event": event,
        "tool": data.get("tool_name") or "",
        "ts": time.time(),
        "state": state,
    }
    os.makedirs(EVENTS_DIR, exist_ok=True)
    path = os.path.join(EVENTS_DIR, pane.lstrip("%") + ".json")
    fd, tmp = tempfile.mkstemp(dir=EVENTS_DIR, suffix=".tmp")
    try:
        os.write(fd, json.dumps(out).encode())
    finally:
        os.close(fd)
    os.replace(tmp, path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
