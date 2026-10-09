"""onboarding — the instructions Arturo's brain runs the first thread from.

The operator, 2026-10-08: "I don't want hardcoded arturo questions, just instructions to the llm that
powers it to ensure a clean onboarding." So no surface scripts a question. While the operator is not
onboarded, a surface marks each turn with a first line `[Onboarding: step=onboarding]` (the page's own
invisible opener uses `step=onboarding_open`). The proxy strips the marker and appends ONE playbook,
built here per turn from facts only the server holds: what is already known about the operator,
whether their team exists and is running, and fixed facts about this release. The brain decides what
to say and ask, and acts through tools (set_operator_fact, ask_choices, create_starter_team,
decline_starter_team, pair_device, check_paired, finish_onboarding).

What the playbook asks is the brain's; what may HAPPEN is the code's (arturo-proxy.py): the team is
created only on the operator turn right after a starter_team card shown on an onboarding turn, a
pairing code only for a device the operator picked on a devices card (the server's record of that
answer) and only on a dashboard (fleet) turn, and the turn after a devices card can record devices and
nothing else. Any non-fleet turn runs a fail-closed tool allowlist. Congruence DEC-1791485978471942 (v4).
"""
from __future__ import annotations

import re

_MARK = re.compile(r"^\[Onboarding:\s*step=([a-z_]+)\]\s*\n?", re.I)

ONBOARDING_STEPS = ("onboarding", "onboarding_open")
# The opener's text, which the page never shows as the operator's words (it hides any user turn that
# starts with this, and the two openers older pages sent).
OPENER_SENTINEL = "(first run:"
LEGACY_OPENERS = ("Introduce my team.", "Explain how seats are organised here.")


def split_marker(text: str):
    """(step | None, text without the marker). Unknown steps are returned so the caller can log
    them; they carry no directive."""
    m = _MARK.match(text or "")
    if not m:
        return None, text
    return m.group(1).lower(), text[m.end():]


# ---- fixed facts: the brain words them, it does not invent them ----------------------------------
ONBOARDING_GUIDE = "https://github.com/Tulum-DAO/orchestraos/blob/main/docs/ONBOARDING.md"
# Only in text-only mode. Dictation is theirs already (the browser's mic permission is its only gate), so
# it is never offered or asked for; Live voice mode (the operator's name for the live call) is the one
# thing that needs a key. A bare "voice mode" names neither, and is not used.
VOICE_FACT = ("Talking: dictation already works for them: the mic button turns their speech into text, "
              "with only their browser's own mic permission. Never offer it, ask about it or call it a "
              "mode. What is not on yet is Live voice mode, where they talk and you talk back "
              "live: in the browser it needs GEMINI_API_KEY on their server (the phone app's call uses its "
              "own voice key, such as ELEVENLABS_API_KEY) and Arturo restarted. Mention it once, briefly, "
              "as something they can turn on later.")
DEFAULT_PROJECT = "first-project"

TEAM_SHAPE = (
    "The starter team is three agents in three tiers. gm is the T0 manager: the one agent the operator talks "
    "to about everything; it is always on, keeps track of every project, hands work to the project managers "
    "and reports back, so the operator does not have to manage each agent. pm-<project> is a T1 project "
    "manager: it runs one project and the workers on it, and reports to gm. dev-<project> is a T2 worker: it "
    "does the actual jobs its project manager gives it. More projects mean more project managers and workers "
    "under the same gm."
)
TEAM_COST = "This starts three agents; gm is always on and keeps costing tokens whether or not it is asked anything."
# The devices card's own words: a pick there is consent to make pairing codes (arturo-proxy.py ask_choices).
DEVICES_NOTE = "Picking an iPhone, iPad or Mac lets Arturo make a pairing code for it in the next few minutes."

# The words a tool result uses for each seat (arturo-proxy.py seat_seen).
SEEN_WORDS = {
    "running": "running",
    "stopped": "a session is open but no agent is running in it",
    "no session": "not started",
    "unknown": "could not tell",
}

# Per device: what is true of this release (docs/ONBOARDING.md "What works today"; INSTALL.md §1), and
# whether Arturo can pair it. No app is public: nothing here is a link, a store listing or a date.
DEVICES = ("iPhone", "iPad", "Apple Watch", "Mac", "Android phone", "Just this computer")
DEVICE_FACTS = {
    "iPhone": "The iPhone app is in testing and not released yet. Tailscale from the App Store, signed in to "
              "the same account, puts the phone on the network; this page then opens in Safari at the same "
              "address. With the test app installed, a code pairs it: in the app, the Arturo tab, then the "
              "gear at the top right, then Connect your gateway, then paste the code and press Pair.",
    "iPad": "The iPad app is in testing and not released yet. Tailscale from the App Store, signed in to the "
            "same account, then this page opens in Safari. With the test app installed, a code pairs it: "
            "Settings in the sidebar, then Connect your gateway, then paste the code and press Pair.",
    "Apple Watch": "The Watch app comes with the iPhone app and takes its pairing from the iPhone, so there is "
                   "nothing to pair on the watch itself; pair the iPhone. It is not released yet either.",
    "Mac": "The Mac app is in testing and not released yet. Tailscale on the Mac (Mac App Store or "
           "tailscale.com/download), signed in to the same account, and this page works in a browser. With the "
           "test app installed, a code pairs it: the Connect this Mac window, paste the code, press Pair.",
    "Android phone": "There is no Android app, so nothing to pair. Tailscale from Google Play, signed in to "
                     "the same account, then this page opens in Chrome at the same address.",
    "Just this computer": "Nothing more to set up: this page is the whole of it.",
}
PAIRABLE = ("iPhone", "iPad", "Mac")
# Where the pairing card says to paste, per device (the same paths DEVICE_FACTS gives the brain).
PASTE_WHERE = {
    "iPhone": "In the iPhone app: the Arturo tab, then the gear at the top right, then Connect your gateway.",
    "iPad": "In the iPad app: Settings in the sidebar, then Connect your gateway.",
    "Mac": "In the Mac app: the Connect this Mac window.",
}

NAME_RULES = (
    "Their answer may be dictated: lower case, no punctuation, filler words. Record a name only when it is "
    "clearly the name they want to be called; if two words may be a first and a last name, ask which they "
    "prefer; if there is no name in it (a greeting, a question, a refusal, a sentence cut off), ask again. "
    "Never invent or guess a name."
)


def seat_line(seats) -> str:
    return "; ".join(f"{s.get('name')} ({s.get('tier')}): {SEEN_WORDS.get(s.get('seen'), 'could not tell')}"
                     for s in (seats or []))


def _team_fact(team) -> str:
    team = team or {}
    state = team.get("state")
    seats = seat_line(team.get("seats"))
    if state == "absent":
        return "Their team: none yet."
    if state == "starting":
        return f"Their team: being set up right now; seen so far: {seats or 'nothing yet'}. Do not offer it again."
    if state == "incomplete":
        project = team.get("project")
        return (f"Their team: started but not complete; seen: {seats or 'no seats'}."
                + (f" Its project is '{project}'." if project else ""))
    if state == "present":
        return f"Their team: up; seen: {seats}. Nothing to create."
    if state == "other_manager":
        return (f"Their team: they already have a manager named {team.get('manager')}, set up some other way; "
                "it plays the gm role. Do not offer the starter team: it would add a second manager.")
    return "Their team: could not be checked (an unread registry is not an empty one). Do not offer it."


def playbook(ctx=None) -> str:
    """The whole onboarding, as instructions, rebuilt every turn from what the server knows. The brain
    is told what is still missing; it decides the words and when to finish."""
    ctx = ctx or {}
    op = ctx.get("operator") or {}
    name, devices = op.get("name"), op.get("devices")
    known = [
        f"Their name: {name}." if name else "Their name: not known yet.",
        f"Their devices: {devices}." if devices else "Their devices: not known yet.",
        _team_fact(ctx.get("team")),
    ]
    if ctx.get("voice_mode") == "text-only":
        known.append(VOICE_FACT)
    if ctx.get("opener"):
        known.append("This turn is the page opening for the first time, not the operator speaking: greet them, "
                     "say in one sentence who you are, and start on the first missing goal.")
    goals = [
        "1. If their name is not known: ask what to call them, and record it with set_operator_fact(field='name'). "
        + NAME_RULES,
        "2. Their team: explain it in their terms (" + TEAM_SHAPE + ") If there is no team, or it is incomplete, "
        "offer it with ask_choices(purpose='starter_team'), options like 'Set it up' and 'Not now', and say the "
        "cost plainly: " + TEAM_COST + " Ask what to call the first project, or use '" + DEFAULT_PROJECT + "'. "
        "On their yes, call create_starter_team; on a clear no, call decline_starter_team. Report each seat "
        "exactly as the tool says it sees it.",
        "3. Their devices: ask which they have with ask_choices(purpose='devices', multi=true, exclusive='"
        + DEVICES[-1] + "'), options such as " + ", ".join(DEVICES)
        + ". Record their answer with set_operator_fact(field='devices'), on the turn right after the card.",
        "4. Then walk them through each device they picked, ONE at a time, in the order picked, from these facts: "
        + " ".join(f"{k}: {v}" for k, v in DEVICE_FACTS.items())
        + " For an iPhone, iPad or Mac they have the test app for, call pair_device(device=<that device>): the "
        "code goes to them in a card you never see, so never repeat, guess or invent a code; tell them where to "
        "paste it. When they say it is done, call check_paired(device_id=<the id the tool gave you>) and report "
        "exactly what it says. If they do not have the app yet, say it is in testing and not released, skip that "
        "device, and say you can pair it later when they ask.",
        "5. When the goals are done, or they want to stop, call finish_onboarding and say this thread is their "
        "front door from here on.",
    ]
    rules = (
        "Rules: one question per turn; plain, short words (at most six sentences); skip any goal already done; "
        "use ask_choices whenever the answer is a choice, writing the options yourself; facts only from this "
        "instruction or what a tool says; what the operator says they see on their screen is the fact; never "
        "tell them to run tmux attach; no download, store or TestFlight links, and no release dates; for "
        f"pairing an app later, the guide is {ONBOARDING_GUIDE}. If they ask something else, answer it "
        "briefly, then return to the next goal."
    )
    return ("ONBOARDING: you are leading this operator's first run. What is known now: " + " ".join(known)
            + " Goals, in order: " + " ".join(goals) + " " + rules)


def directive(step, ctx=None) -> str:
    """The step's instruction for this turn, or '' for none. Only the onboarding steps carry one."""
    if (step or "").lower() in ONBOARDING_STEPS:
        return playbook({**(ctx or {}), "opener": (step or "").lower() == "onboarding_open"})
    return ""
