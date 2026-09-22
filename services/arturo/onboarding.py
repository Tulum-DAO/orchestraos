"""onboarding — the server-side half of Arturo's first thread.

A surface marks an onboarding turn with a first line `[Onboarding: step=<step>]` (the same shape as
the `[Context: …]` line the pill sends). The proxy strips the marker and appends the step's
directive to the system context, so the instruction for "listen for the operator's name" lives in
ONE place — here — and no surface parses a reply. The brain answers the turn; when it learns a
fact it calls set_operator_fact (operator_store.TOOL), and the surface advances when that tool
name appears in tools_called, exactly as the first-agent step advances on spawn_agent.
"""
from __future__ import annotations

import re

_MARK = re.compile(r"^\[Onboarding:\s*step=([a-z_]+)\]\s*\n?", re.I)

DIRECTIVES = {
    "name": (
        "ONBOARDING, step 'name': you just asked the operator \"What should I call you?\" and this is their "
        "reply, possibly dictated (lower case, no punctuation, filler words). If it contains the name they want "
        "to be called, call set_operator_fact(field='name', value=<that name only>) and greet them by it in one "
        "short sentence. If it contains no name (a greeting, a question, a refusal, or a sentence that was cut "
        "off), do NOT call the tool: ask again plainly in one sentence. If it is unclear which words are the "
        "name (for example two words that may be a first and a last name), do NOT call the tool: ask which "
        "they prefer. Never invent or guess a name."
    ),
}


def split_marker(text: str):
    """(step | None, text without the marker). Unknown steps are returned so the caller can log
    them; they carry no directive."""
    m = _MARK.match(text or "")
    if not m:
        return None, text
    return m.group(1).lower(), text[m.end():]


HIERARCHY_TIERS = (
    "Seats come in three tiers: T2 workers do the jobs, T1 coordinators run a lane of workers, and T0 is "
    "the single always-on manager that runs the whole fleet for you."
)


def _hierarchy(ctx) -> str:
    """Generated per turn, because whether to OFFER a manager depends on whether one exists — a fact
    only the server holds. Three states, never two: known-absent offers, known-present names it, and
    UNKNOWN (an unreadable registry) explains without offering, because "could not check" is not "no"."""
    ctx = ctx or {}
    seat = str(ctx.get("seat") or "").strip()
    manager, known = ctx.get("manager"), bool(ctx.get("manager_known"))
    head = (
        "ONBOARDING, step 'hierarchy': the operator has just watched their first seat come up"
        + (f" ({seat})" if seat else "")
        + ". Explain the shape of the system in at most three short sentences, about 60 words, in their "
        "terms. Use these facts and no others: " + HIERARCHY_TIERS + " "
        + (f"Name their new seat {seat} as the T2 worker they already have. " if seat else "")
    )
    if manager:
        tail = (
            f"A manager already exists on this install: {manager}. Say so in one clause and DO NOT OFFER "
            "to create another — there is one per install. Ask no question; close by saying they can ask "
            "you for status any time."
        )
    elif known:
        tail = (
            "They have no manager yet. Ask exactly ONE question: whether to create it now. Say plainly "
            "that it is always on, which means it keeps costing tokens whether or not it is asked "
            "anything. If they say yes, call spawn_agent with kind='manager' and session_name='gm'. If "
            "they say no, accept it in one clause and do not ask again."
        )
    else:
        tail = (
            "Whether a manager already exists could not be checked on this install, so DO NOT OFFER to "
            "create one — an unchecked registry is not an empty one. Say a manager can be added later "
            "from the Agents page. Ask no question."
        )
    return head + tail + (
        " Answer from the facts in this instruction ONLY: do not call any tool, do not look the fleet up, "
        "and do not report how many seats exist or which machines are online — that is not what this turn "
        "is for. Do not list features, do not pitch, do not mention tiers you were not given, and do not "
        "exceed one question."
    )


def directive(step, ctx=None) -> str:
    """ctx is only read by steps that need a server-side fact (hierarchy). The one-arg call still works."""
    if (step or "").lower() == "hierarchy":
        return _hierarchy(ctx)
    return DIRECTIVES.get(step or "", "")
