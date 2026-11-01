"""history_sanitize.py: refuse to re-present a conversation a model API would reject (broken tool-call pairs).

Ported to Python from CopilotKit/OpenBot `toolkit/src/history-sanitize.ts` (commit 743649e).
Copyright (c) 2026 CopilotKit. MIT, see licenses/copilotkit-openbot-MIT.txt.

Applied to every window of history before it goes into a packet. One dangling tool call poisons every later turn
that replays it, and dropping is the right answer, not repair: history here is context for a turn, not a
transaction to resume, and a call whose turn ended has no result to invent.

Messages are dicts: {"id", "role" ("user"|"assistant"|"tool"|"system"), "content", "tool_calls": [{"id", ...}],
"tool_call_id"}. The rules, in order:
  1. A tool call is ANSWERED if a later message carries it as `tool_call_id` before the next user message, or if the
     caller says it is answered elsewhere. Later, not merely present; before the next user message, because that is
     where a model API stops looking. Only a user row is a boundary.
  2. An assistant message keeps only its answered calls. If that leaves it with no calls and nothing said, it is
     dropped: an empty assistant husk is itself invalid for some providers. (Added here: an assistant message with
     no calls and nothing said is dropped too.)
  3. A tool result survives only as THE answer to a surviving call: the first one after the call and before the
     boundary. Anything else carrying a `tool_call_id` is dropped (an orphan, a result ahead of its call, a second
     answer).
Order is preserved, the input list is not mutated, ids are never changed, and a message the pass does not change is
returned as the same object. Nothing is deleted from any store: this is a read-side filter on one window.
"""
from __future__ import annotations


def _silent(message: dict) -> bool:
    """Whether a message said nothing at all: no text, no parts."""
    content = message.get("content")
    if content is None:
        return True
    if isinstance(content, (str, list)):
        return len(content) == 0
    return False


def sanitize_history(history: list[dict], answered_elsewhere=frozenset()) -> list[dict]:
    n = len(history)
    # Where a call's answer may still land: before the next thing a person said.
    boundary_after = [n] * n
    nxt = n
    for index in range(n - 1, -1, -1):
        boundary_after[index] = nxt
        if history[index].get("role") == "user":
            nxt = index
    # Where each call was made: the first assistant row carrying its id.
    call_at: dict = {}
    for index, message in enumerate(history):
        for call in message.get("tool_calls") or []:
            cid = call.get("id")
            if cid is not None and cid not in call_at:
                call_at[cid] = index
    # The one position that answers each call: the first result after the call and before the boundary.
    answer_at: dict = {}
    for index, message in enumerate(history):
        tcid = message.get("tool_call_id")
        if tcid is None or tcid in answer_at:
            continue
        called = call_at.get(tcid)
        if called is None or index <= called:
            continue
        if index >= boundary_after[called]:
            continue
        answer_at[tcid] = index

    surviving: set = set()
    kept: list = []
    for message in history:
        calls = message.get("tool_calls")
        if calls is None:
            # OrchestraOS addition (not in the source): an assistant row that says nothing and calls nothing is the
            # same invalid husk, reached without a call at all (an interrupted reply leaves one in our transcripts).
            kept.append(None if message.get("role") == "assistant" and _silent(message) else message)
            continue
        answered = [c for c in calls if c.get("id") in answered_elsewhere or c.get("id") in answer_at]
        for c in answered:
            surviving.add(c.get("id"))
        # The husk check goes first, so it also catches a row that arrived with no calls and nothing said.
        if not answered and _silent(message):
            kept.append(None)
            continue
        if len(answered) == len(calls):
            kept.append(message)                     # the healthy path: the very same object
            continue
        if answered:
            kept.append({**message, "tool_calls": answered})
            continue
        rest = {k: v for k, v in message.items() if k != "tool_calls"}   # what it said, minus the call
        kept.append(rest)

    out = []
    for index, message in enumerate(kept):
        if message is None:
            continue
        tcid = message.get("tool_call_id")
        if tcid is None or (tcid in surviving and answer_at.get(tcid) == index):
            out.append(message)
    return out
