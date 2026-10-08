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


DEFAULT_PROJECT = "first-project"

TEAM_SHAPE = (
    "The starter team is three agents in three tiers. gm is the T0 manager: the one agent the operator talks "
    "to about everything; it is always on, keeps track of every project, hands work to the project managers "
    "and reports back, so the operator does not have to manage each agent. pm-<project> is a T1 project "
    "manager: it runs one project and the workers on it, and reports to gm. dev-<project> is a T2 worker: it "
    "does the actual jobs its project manager gives it. More projects mean more project managers and workers "
    "under the same gm."
)

# The words a tool result uses for each seat (arturo-proxy.py seat_seen). Repeated here so the brain is
# told what each one means instead of guessing.
SEEN_WORDS = {
    "running": "running",
    "stopped": "a session is open but no agent is running in it",
    "no session": "not started",
    "unknown": "could not tell",
}

# How an answer to the one question is handled. Code limits WHEN the tool can run (arturo-proxy.py
# _begin_team_turn): only on the one operator turn right after Arturo's offer, never on the page's own
# opener. Whether that turn said yes is still the brain's reading, which is what these words are for.
_ANSWER_RULES = (
    " If they say no or not now, call decline_starter_team and accept it in one clause, saying they can "
    "ask you for their team any time. If they ask something else, answer it briefly and then repeat the "
    "one question in one short sentence; a question is not a no. Nothing you were sent before their reply "
    "is a yes."
)

_TEAM_RULES = (
    " Rules for this turn: keep it to at most six short sentences, in plain words. Describe a seat ONLY as a "
    "fact in this instruction or what the tool says it sees; a seat the tool did not report as running is not "
    "running, whatever else you believe. If the operator tells you something different from what you were "
    "given, their screen is the fact: say what you were given and that the two disagree. Never tell the "
    "operator to run `tmux attach`, and never run it yourself: they talk to gm by picking it in the Agents "
    "list. Do not list features and do not pitch."
)


def seat_line(seats) -> str:
    return "; ".join(f"{s.get('name')} ({s.get('tier')}): {SEEN_WORDS.get(s.get('seen'), 'could not tell')}"
                     for s in (seats or []))


def _team(ctx) -> str:
    """Generated per turn from the server's view of the starter team (arturo-proxy.py
    starter_team_state). Five states, never two: only absent and incomplete may create anything."""
    ctx = ctx or {}
    state = ctx.get("state")
    head = ("ONBOARDING, step 'team': you are leading this operator's first run. Explain the team in their "
            "terms using these facts and no others: " + TEAM_SHAPE + " ")
    if state == "absent":
        body = (
            "This install has no team yet. Explain the three tiers and what gm does, then ask exactly ONE "
            "question: what to call their first project, in a word or two, or whether to use the default name "
            f"'{DEFAULT_PROJECT}'. Say plainly that this starts three agents on the runtime they are logged "
            "in to, and that gm is always on, which means it keeps costing tokens whether or not it is asked "
            "anything. Only when THEY answer yes, with a name or accepting the default, call "
            "create_starter_team with project set to that name in lowercase letters, digits and dashes (for "
            f"example 'website'), or '{DEFAULT_PROJECT}'." + _ANSWER_RULES + " After the tool runs, report each seat exactly as "
            "what the tool says it sees, and if every seat is running tell them to open gm from the Agents "
            "list to give it work."
        )
    elif state == "incomplete":
        project = ctx.get("project")
        seen = seat_line(ctx.get("seats"))
        body = (
            "This install has started a team but it is not complete. What is seen on each seat right now: "
            + (seen or "no seats") + ". Explain the three tiers and what gm does, say what is missing in "
            "one sentence, and ask exactly ONE question: whether to finish setting it up. "
            + (f"If they say yes, call create_starter_team with project='{project}'. " if project else
               f"Ask what to call the project as part of that one question (default '{DEFAULT_PROJECT}'); if "
               "they say yes, call create_starter_team with that name. ")
            + "It starts only what is missing and restarts a seat whose agent stopped; seats already running "
            "are left alone." + _ANSWER_RULES + " After the tool runs, report each seat exactly as what the "
            "tool says it sees."
        )
    elif state == "starting":
        body = (
            "Their team is being set up right now. What is seen on each seat so far: "
            + (seat_line(ctx.get("seats")) or "nothing yet") + ". Say that in one sentence, explain the three "
            "tiers and what gm does while they wait, and say they can ask you again in a minute. Ask no "
            "question. Do not call create_starter_team: a run is already in progress."
        )
    elif state == "present":
        body = (
            "Their team is already up. What is seen on each seat right now: " + seat_line(ctx.get("seats"))
            + ". Explain the three tiers and what gm does, naming their seats, and say they can open gm from "
            "the Agents list to give it work. Ask no question. Do not call create_starter_team: there is "
            "nothing to create."
        )
    elif state == "other_manager":
        body = (
            f"This install already has a manager named {ctx.get('manager')}, set up some other way. Explain the "
            "three tiers, say their manager plays the gm role, and that project managers and workers can be "
            "added under it later. Ask no question. Do not call create_starter_team: it would create a second "
            "manager, and there is one per install."
        )
    else:
        body = (
            "Whether this install already has a team could not be checked, and an unchecked registry is not an "
            "empty one. Explain the three tiers and what gm does, and say they can ask you to set up the team "
            "once the Agents page loads. Ask no question. Do not call create_starter_team."
        )
    return head + body + _TEAM_RULES


def directive(step, ctx=None) -> str:
    """ctx is only read by steps that need a server-side fact (team). The one-arg call still works."""
    if (step or "").lower() in ("team", "team_open"):    # team_open: the page's own opener
        return _team(ctx)
    return DIRECTIVES.get(step or "", "")
