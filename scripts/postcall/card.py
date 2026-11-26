"""The review card: one questionnaire per call, paged only past 19 items (design §4).

Questions persist only prompt / kind / menu / required (questionnaire_schema.py:194-202), so the
evidence rides in the prompt and the bulk rules in Q1's option details. The option list and the
suggested option are frozen per question; answers are only ever read against that frozen copy."""
import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from .deadlines import Due

PAGE_ITEMS = 19
_Q1 = [
    ("Accept every suggested choice (⚠ items need your own answer)",
     "Unanswered items get their suggested choice, except ⚠ items, which stay unreviewed. An answer you give an item always wins."),
    ("Decide per item (unanswered = nothing happens)",
     "Only the items you answer are acted on; the rest are kept as unreviewed."),
    ("Drop all", "Unanswered items are dropped. An answer you give an item still wins."),
]


@dataclass
class CardItem:
    key: str
    owner: str               # shaw | participant
    text: str
    counterpart: str
    due: Due
    quote: str
    citation: str
    trust: str               # verified | inferred | unknown
    seat: str | None = None
    agent_work: str | None = None
    chase_date: str | None = None
    uncertain_match: dict | None = None
    route: bool = False      # no client seat resolved: the seat (gm) routes the draft


@dataclass
class Page:
    title: str
    summary: str
    questions: list
    frozen: list
    decide_by: str | None = None    # ISO; the 14-day retire counts from it (gm ruling §9.6)


def _local(iso, tz, label):
    when = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo(tz))
    return when.strftime("%a %d %b %H:%M ") + label


def _due_text(due, tz, label):
    return _local(due.due_ts, tz, label) if due.due_ts else due.due_source


def _flagged(it):
    return it.trust != "verified" or it.uncertain_match is not None


def _options(it, tz, label):
    """[(label, frozen_action)], suggested first."""
    if it.owner == "participant":
        opts = [(f"Track it: chase {it.counterpart} on {_local(it.chase_date, tz, label)}",
                 {"action": "track", "chase_date": it.chase_date})]
        if it.seat:
            opts.append((f"Assign to {it.seat}: draft a follow-up nudge for me to send",
                         {"action": "assign", "seat": it.seat, "agent_work": "draft a follow-up nudge"}))
    elif it.seat and it.agent_work and it.route:
        opts = [(f"Assign to {it.seat} to route: {it.agent_work} (you send it)",
                 {"action": "assign", "seat": it.seat, "agent_work": it.agent_work, "route": True}),
                ("Take it myself", {"action": "take"})]
    elif it.seat and it.agent_work:
        opts = [(f"Assign to {it.seat}: {it.agent_work}",
                 {"action": "assign", "seat": it.seat, "agent_work": it.agent_work}),
                ("Take it myself", {"action": "take"})]
    else:
        opts = [("Take it myself", {"action": "take"})]
    opts.append(("Drop", {"action": "drop"}))
    if it.uncertain_match:
        m = it.uncertain_match
        opts.append((f'Same as "{m["text"]}" ({m["date"]}): update it', {"action": "same_as", "cmt": m["cmt"]}))
    opts[0] = (opts[0][0] + " (suggested)", opts[0][1])
    return opts


def _prompt(it, tz, label):
    head = (f"{it.counterpart} owes you: {it.text} · Due: {_due_text(it.due, tz, label)}" if it.owner == "participant"
            else f"{it.text} · {it.counterpart} · Due: {_due_text(it.due, tz, label)}")
    return f"{'⚠ ' if _flagged(it) else ''}{head}\n\"{it.quote[:200]}\" ({it.citation})"


def _order(items):
    def k(it):
        return (it.owner != "shaw", it.due.due_ts is None,
                dt.datetime.fromisoformat(it.due.due_ts) if it.due.due_ts else dt.datetime.max.replace(tzinfo=dt.timezone.utc))
    return sorted(items, key=k)


def build(*, title, items, tz, tz_label, call_start, call_end, participants, now=None):
    if not items:
        raise ValueError("refusing to build a post-call card with no items")
    ordered = _order(items)
    dues = [it for it in ordered if it.due.due_ts]
    if dues:
        first = min(dues, key=lambda it: dt.datetime.fromisoformat(it.due.due_ts))
        decide = dt.datetime.fromisoformat(first.due.due_ts) - dt.timedelta(hours=2)
        cost = f"{first.text} slips"
    else:
        decide = dt.datetime.fromisoformat(call_end.replace("Z", "+00:00")) + dt.timedelta(hours=24)
        cost = "the items above go untracked"
    if now is not None:     # a backlog or replayed call: never a Decide-by in the past (gm msg_feff515d)
        at = dt.datetime.fromisoformat(str(now).replace("Z", "+00:00"))
        if decide < at:
            decide = at + dt.timedelta(hours=24)
    start = dt.datetime.fromisoformat(call_start.replace("Z", "+00:00"))
    minutes = round((dt.datetime.fromisoformat(call_end.replace("Z", "+00:00")) - start).total_seconds() / 60)
    chunks = [ordered[i:i + PAGE_ITEMS] for i in range(0, len(ordered), PAGE_ITEMS)]
    pages = []
    for n, chunk in enumerate(chunks, 1):
        paged = len(chunks) > 1
        line1 = (f"Decide by: {_local(decide.isoformat(), tz, tz_label)} · If no answer: nothing is created or "
                 f"dispatched · Cost of waiting: {cost}")
        line2 = (f"From: {title} · {_local(call_start, tz, tz_label)} · {minutes} min · With: {', '.join(participants)}"
                 + (f" · Page {n} of {len(chunks)}" if paged else ""))
        questions = [{"prompt": "How should I handle the items below?", "kind": "menu", "required": True,
                      "menu": {"options": [{"label": lab, "detail": det} for lab, det in _Q1]}}]
        frozen = []
        for q_n, it in enumerate(chunk, 2):
            opts = _options(it, tz, tz_label)
            questions.append({"prompt": _prompt(it, tz, tz_label), "kind": "menu", "required": False,
                              "menu": {"options": [{"label": lab} for lab, _ in opts]}})
            frozen.append({"q_n": q_n, "key": it.key, "options": [a for _, a in opts], "suggested_n": 1,
                           "flagged": _flagged(it),
                           "item": {"owner": it.owner, "text": it.text, "counterpart": it.counterpart,
                                    "due_ts": it.due.due_ts, "due_source": it.due.due_source, "quote": it.quote,
                                    "citation": it.citation, "call_title": title}})
        pages.append(Page(title=f"{title}: {len(items)} items" + (f" (page {n} of {len(chunks)})" if paged else ""),
                          summary=f"{line1}\n{line2}", questions=questions, frozen=frozen,
                          decide_by=decide.isoformat()))
    return pages
