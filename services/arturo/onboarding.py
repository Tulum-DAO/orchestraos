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


def directive(step) -> str:
    return DIRECTIVES.get(step or "", "")
