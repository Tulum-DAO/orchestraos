#!/usr/bin/env python3
"""bus_feeder.py — Claude Code hook: FEED the WS-A event bus (UNARMED until registered).

The append-side of the WS-A bus arming wiring (gm/the operator commission msg_85983af9).
A registered Stop/UserPromptSubmit/PreToolUse/PostToolUse/SessionStart/SessionEnd/
Notification hook that maps the real hook_event_name -> a canonical bus event and
appends it to the append-only stream (bus.append_event). The turn-boundary edge
(Stop -> turn_ended) is the delivery-relevant one.

HARD CONTRACT (modeled on state-event-hook.py — the Tier-0 truth hook):
  * ALWAYS exit 0. A hook exit 2 blocks the turn (PreToolUse/Stop); we NEVER block.
  * NEVER print to stdout (UserPromptSubmit stdout is injected as agent context).
  * FAIL-OPEN / fail-silent: any error (bad stdin, ENOSPC, permission, import) is
    swallowed — a dropped event degrades to delayed-not-lost, NEVER a wedged agent.
  * RESOLVE-FREE: the hook does NOT resolve pane->canonical agent (no registry in
    the hook); it writes source identity (pane/session/cwd) + agent=None. The bus
    resolves the agent at INGEST (Q2 lock).

This file is INERT until it is registered in ~/.claude/settings.json (the arm-action
delivered to gm). Present-but-unregistered = no effect. It is import-safe.
"""
import json
import os
import sys

# The bus append-writer + schema live in the package; import lazily inside main so
# a broken import can NEVER escape the fail-open boundary.
STREAM_DIR = os.environ.get(
    "ORCH_EVENT_STREAM_DIR",
    os.path.join(os.environ.get("ORCHESTRA_DIR") or os.environ.get("ORCH_DIR") or os.path.expanduser("~/orchestra"), "state", "event-stream"))


def _feed(stdin_text: str) -> dict:
    """Pure core (testable): parse a hook payload + return the raw event that WOULD
    be appended, or {} if this event maps to nothing. Does NO IO. Raises nothing
    the caller needs to catch for correctness — but main() wraps it fail-open anyway."""
    from scripts.focus_registry.event_schema import hook_to_type, build_raw_event
    data = json.loads(stdin_text) if stdin_text.strip() else {}
    event_name = data.get("hook_event_name") or ""
    etype = hook_to_type(event_name)
    if not etype:
        return {}
    pane = os.environ.get("TMUX_PANE")
    payload = {}
    if etype == "tool_use":
        payload = {"tool": data.get("tool_name") or "",
                   "phase": "post" if event_name == "PostToolUse" else "pre"}
    elif etype == "session_start":
        payload = {"source": data.get("source") or ""}
    elif etype == "turn_ended":
        payload = {}
    elif etype == "notification":
        msg = (data.get("message") or "").lower()
        payload = {"kind": "permission" if ("permission" in msg or "approv" in msg)
                   else "other"}
    return build_raw_event(etype, pane=pane, session_id=data.get("session_id"),
                           cwd=data.get("cwd"), payload=payload)


def main() -> None:
    """Read the hook payload from stdin, append the mapped event to the stream.
    Every step is best-effort; the module-level guard guarantees exit 0."""
    # No pane => not a tmux agent turn => nothing to feed.
    if not os.environ.get("TMUX_PANE"):
        return
    try:
        stdin_text = sys.stdin.read()
    except Exception:  # noqa: BLE001
        return
    try:
        raw = _feed(stdin_text)
    except Exception:  # noqa: BLE001 -- malformed payload / import error: drop it
        return
    if not raw:
        return
    try:
        from scripts.lineage_daemon import bus
        bus.append_event(raw, stream_dir=STREAM_DIR)   # itself fail-closed on validate
    except Exception:  # noqa: BLE001 -- ENOSPC/permission/import: delayed-not-lost
        return


if __name__ == "__main__":
    # Ensure the package is importable when run as a hook (cwd-independent).
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    try:
        main()
    except Exception:  # noqa: BLE001 -- the outermost fail-open guard
        pass
    sys.exit(0)   # ALWAYS 0 — never block a turn
