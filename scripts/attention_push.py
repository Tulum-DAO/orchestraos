"""attention_push.py — should this attention item buzz the person's devices? One server-side decision.

decide_push(item, prefs, now_utc, tz, level=None, agent_level=None) -> {'decision', 'reason'} | None
  push  send it now
  list  show it in the list quietly (the person does not want an alert for this one)
  held  quiet hours: alert once they end
None for answered items (nothing to alert about).

Part 1 always passes level=None: today's Settings > Notifications decide, through notify_policy.decide
(ok -> push, suppressed -> list, quiet -> held). Part 2 passes the supervision level (the agent's own level
wins over the person's), then quiet hours still turn a push into held:
  let_them_run   push only blocking items, high-risk items and failures
  check_in       push what is waiting on the person; list updates
  watch_closely  push everything

apply_reminder() is the override a fired "remind me" rule puts on an item: a quiet 'list' becomes 'push',
but quiet hours still hold it and a person or agent set to Nothing still wins, so a reminder never breaks
the person's own mute.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notify_policy  # noqa: E402

#: notify_policy's event kinds. stuck counts as "failed" (its doc: "failed (failed or stuck)").
EVENT_FOR_KIND = {"decision": "needs_me", "questionnaire": "needs_me", "permission": "needs_me",
                  "human_task": "needs_me", "done": "finished", "failed": "failed", "stuck": "failed",
                  "missed_call": "missed_call", "on_behalf": "finished"}
LEVELS = ("let_them_run", "check_in", "watch_closely")

_FROM_POLICY = {notify_policy.OK: "push", notify_policy.SUPPRESSED: "list", notify_policy.QUIET: "held"}


def _level_wants_push(level, item) -> bool:
    if level == "watch_closely":
        return True
    if level == "check_in":
        return item.get("tab") == "waiting"
    return bool(item.get("blocking")) or item.get("risk") == "high" or item.get("kind") == "failed"


def decide_push(item, prefs, now_utc, tz, *, level=None, agent_level=None):
    if item.get("tab") == "answered":
        return None
    prefs = prefs if isinstance(prefs, dict) else {}
    if level is None and agent_level is None:
        event = EVENT_FOR_KIND.get(item.get("kind"), "finished")
        got = notify_policy.decide(prefs, event, item.get("agent"), now_utc, tz)
        reason = ("quiet_hours" if got == notify_policy.QUIET
                  else f"notify_level={notify_policy.level_for(prefs, item.get('agent'))}")
        return {"decision": _FROM_POLICY[got], "reason": reason}
    for lv in (level, agent_level):
        if lv is not None and lv not in LEVELS:
            raise ValueError(f"unknown supervision level {lv!r} (want one of {LEVELS})")
    use, reason = (agent_level, f"agent_level={agent_level}") if agent_level else (level, f"level={level}")
    if not _level_wants_push(use, item):
        return {"decision": "list", "reason": reason}
    if notify_policy.in_quiet_hours(prefs.get("quiet_hours"), now_utc, tz):
        return {"decision": "held", "reason": "quiet_hours"}
    return {"decision": "push", "reason": reason}


def apply_reminder(push, reminder, prefs, agent, now_utc, tz):
    """The item's push after a fired reminder ({rule_id, n}); unchanged when there is none."""
    if push is None or not reminder:
        return push
    prefs = prefs if isinstance(prefs, dict) else {}
    if notify_policy.level_for(prefs, agent) == "nothing":
        return {"decision": "list", "reason": "notify_level=nothing"}
    if notify_policy.in_quiet_hours(prefs.get("quiet_hours"), now_utc, tz):
        return {"decision": "held", "reason": "quiet_hours"}
    return {"decision": "push", "reason": f"reminder:{reminder.get('rule_id')}", "reminder_n": reminder.get("n", 1)}
