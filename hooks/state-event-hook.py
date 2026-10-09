#!/usr/bin/env python3
"""state-event-hook.py — Claude Code hook: push-based agent-state ground truth.

Installed by `orchestra init` into ~/.claude/settings.json for UserPromptSubmit, PreToolUse,
PostToolUse, Stop, SessionStart, SessionEnd and Notification. Writes one small
JSON per tmux pane to state/agent-events/panes/<pane>.json — truth the TUI
cannot lie about (see docs/agent-state-truth-audit.md, Tier 0). Consumed by
scripts/agent-status.py.

Also keeps state/agent-events/calls/<pane>.json: the pane's OPEN tool calls
{tool_use_id: {tool, ts, questions?, file?}} (menu instance identity, DEC-1791405753559307
phase 0). PreToolUse adds, PostToolUse drops, SessionStart / SessionEnd clear. Stop does NOT clear: background
subagents outlive the main agent's Stop, and clearing their calls let a later same-tool
call own their menu. A call that never closes (denied, interrupted) lingers until the TTL;
that can only make a menu ambiguous (no instance), never give it a wrong one. A menu on screen is
stamped with the id of the ONE open call that matches it (scripts/menu_instance.py).

HARD CONTRACT: always exit 0, never print to stdout (UserPromptSubmit stdout is
injected as context; PreToolUse/Stop exit 2 would block the agent). Fail silent.
"""
import fcntl
import json
import os
import sys
import tempfile
import time

_DATA = os.environ.get("ORCHESTRA_DIR") or os.environ.get("ORCH_DIR") or os.path.expanduser("~/orchestra")
EVENTS_DIR = os.environ.get("ORCH_EVENTS_DIR", os.path.join(_DATA, "state", "agent-events", "panes"))

CALLS_DIR = os.environ.get(
    "ORCH_CALLS_DIR",
    os.path.join(os.path.dirname(EVENTS_DIR.rstrip("/")), "calls"))
CALLS_CAP = 16
CALLS_TTL_S = 1800
_CLEAR_ON = ("SessionStart", "SessionEnd")

STATE_MAP = {
    "UserPromptSubmit": "working",
    "PreToolUse": "working",
    "PostToolUse": "working",
    "PreCompact": "working",
    "Stop": "idle",
    "SessionStart": "idle",
    "SessionEnd": "stopped",
}


def _call_entry(data, now):
    """What a menu can be matched against. Only short, derived fields: never the full input."""
    tool = data.get("tool_name") or ""
    inp = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    e = {"tool": tool, "ts": now}
    if tool == "AskUserQuestion":
        qs = [q.get("question") for q in (inp.get("questions") or []) if isinstance(q, dict)]
        e["questions"] = [q[:300] for q in qs if isinstance(q, str)]
    fp = inp.get("file_path") or inp.get("notebook_path")
    if isinstance(fp, str) and fp:
        e["file"] = os.path.basename(fp)[:200]
    return e


def update_open_calls(pane, data, event, now=None):
    """Maintain the pane's open-call map under a per-pane lock (parallel tools fire concurrently)."""
    now = time.time() if now is None else now
    tid = data.get("tool_use_id")
    if event == "PreToolUse" and not tid:
        return
    if event not in ("PreToolUse", "PostToolUse") and event not in _CLEAR_ON:
        return
    os.makedirs(CALLS_DIR, exist_ok=True)
    name = pane.lstrip("%")
    path = os.path.join(CALLS_DIR, name + ".json")
    with open(os.path.join(CALLS_DIR, name + ".lock"), "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            with open(path) as f:
                calls = json.load(f)
            if not isinstance(calls, dict):
                calls = {}
        except (OSError, ValueError):
            calls = {}
        if event in _CLEAR_ON:
            if not calls:
                return
            calls = {}
        elif event == "PreToolUse":
            calls[tid] = _call_entry(data, now)
        elif tid in calls:
            del calls[tid]
        else:
            return
        calls = {k: v for k, v in calls.items()
                 if isinstance(v, dict) and now - (v.get("ts") or 0) < CALLS_TTL_S}
        if len(calls) > CALLS_CAP:
            calls = dict(sorted(calls.items(), key=lambda kv: kv[1].get("ts") or 0)[-CALLS_CAP:])
        fd, tmp = tempfile.mkstemp(dir=CALLS_DIR, suffix=".tmp")
        try:
            try:
                os.write(fd, json.dumps(calls).encode())
            finally:
                os.close(fd)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def main() -> None:
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    event = data.get("hook_event_name") or ""
    try:
        update_open_calls(pane, data, event)
    except Exception:
        pass        # the state file below must still be written; identity is best-effort

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
        # The CLI's own transcript location. cwd is where the hook FIRED, and it follows every
        # `cd`; the transcript lives under the directory the session STARTED in. Measured
        # 2026-10-07: 3 of 31 fresh seats had cd'd away, so a path rebuilt from cwd missed.
        "transcript_path": data.get("transcript_path"),
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
