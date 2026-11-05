"""attention_items.py — one item shape for everything that needs a person's attention. Pure mappers, no I/O.

Every surface (web, phone, watch) reads the same list from GET /attention, so the kind, blocking, state,
surface and action rules live here once:

  from_ledger(row)          a pending ledger card (decision, permission menu, human_task)   tab waiting
  from_questionnaire(row)   a pending questionnaire                                         tab waiting
  from_permission(row)      a live pane permission prompt (perm: pseudo-row)                tab waiting
  from_event(row)           an attention_events row (done, failed, stuck, missed_call, on_behalf)  tab update
  from_history(row)         an ended card or questionnaire                                  tab answered

The required fields are id, tab, kind, agent, created_at, blocking, risk, urgency, actions, state,
answered_by, answer_surface, answered_at, seen_at and push. Everything else is additive.

Completion cards are never items of their own: each one is the linked card of a `done` Update (the mirror
in attention_store), so it is left out of the waiting and answered tabs alike.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attention_push  # noqa: E402

SURFACES = ("web", "phone", "watch", "chat", "rule")
_ANSWERED_STATUSES = ("answered", "resumed", "resolved_elsewhere", "resume_failed", "discarded",
                      "submitted", "stale_target")


@dataclass(frozen=True)
class Ctx:
    person: str
    prefs: dict = field(default_factory=dict)
    tz: str = ""
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    seen: dict = field(default_factory=dict)            # item id -> (seen_at, surface)
    cards: dict = field(default_factory=dict)           # linked completion card id -> {status, answered_at, ...}
    agent_states: dict = field(default_factory=dict)    # agent id -> /agents state (working, idle, ...)
    reminders: dict = field(default_factory=dict)       # item id -> {rule_id, n} (a fired "remind me")
    on_behalf: dict = field(default_factory=dict)       # item id -> {by, rule_id, action, chosen, reasoning}
    urgency: dict = field(default_factory=dict)         # questionnaire id -> urgency (its own column)
    push_fn: Callable = attention_push.decide_push

    def with_cards(self, cards) -> "Ctx":
        return dataclasses.replace(self, cards=dict(cards))


def _a(type_, label, watch_ok, needs_text="none", **kw):
    return {"type": type_, "label": label, "watch_ok": watch_ok, "needs_text": needs_text, **kw}


def _parse(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _menu(row):
    m = _parse(row.get("menu"))
    return m if isinstance(m, dict) else None


def _is_permission_menu(menu) -> bool:
    bridge = (menu or {}).get("bridge")
    return isinstance(bridge, dict) and bridge.get("permission") is True


def kind_of(row) -> str | None:
    """The item kind of a ledger/feed row; None for a completion card (it belongs to a done Update)."""
    k = row.get("kind")
    if k == "completion":
        return None
    if k == "questionnaire" or str(row.get("id", "")).startswith("qnr_"):
        return "questionnaire"
    if k == "permission" or str(row.get("id", "")).startswith("perm:"):
        return "permission"
    if k == "human_task":
        return "human_task"
    if k == "menu" and _is_permission_menu(_menu(row)):
        return "permission"
    return "decision"


def _int_or_none(v):
    try:
        return int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _blocking(row, kind, ctx) -> bool:
    """"An agent is stopped on it": a held permission or menu, a human_task that blocks something, and a plain
    approval only while its agent is not working (it asked and is idle on the answer)."""
    if kind == "permission":
        return True
    if kind == "human_task":
        return bool(row.get("blocks_what")) and row.get("feature") != "commitment"
    if kind != "decision":
        return False
    if row.get("kind") == "menu":
        return True
    if row.get("kind") in (None, "", "approval"):
        return ctx.agent_states.get(row.get("from_agent")) != "working"
    return False                                         # proposal shadow rows: nobody is parked on them


def _multi(menu) -> bool:
    parts = menu.get("parts")
    return bool(menu.get("checkbox")) or (_int_or_none(menu.get("part_count")) or 1) > 1 or (
        isinstance(parts, list) and len(parts) > 1)


def _option_actions(menu):
    out = []
    for o in menu.get("options") or []:
        if not isinstance(o, dict) or o.get("n") in (None, ""):
            continue
        ik = o.get("input_kind")
        out.append(_a("option", str(o.get("label") or ""), ik != "chat",
                      "required" if ik == "free_text" else "none", n=str(o["n"])))
    return out


def actions_for(kind, row=None, *, linked_pending=False) -> list:
    row = row or {}
    if kind == "decision":
        menu = _menu(row)
        if row.get("kind") == "menu" and menu:
            return [_a("submit", "Answer", True)] if _multi(menu) else _option_actions(menu)
        return [_a("approve", "Approve", True), _a("deny", "Deny", True, "optional"),
                _a("respond", "Respond", True, "required")]
    if kind == "permission":
        menu = _menu(row) or {}
        if row.get("answer_in_terminal") or menu.get("answer_in_terminal"):
            return [_a("open_agent", "Open in terminal", False)]
        if str(row.get("id", "")).startswith("perm:"):
            return [_a("key", str(o.get("label") or ""), True, n=str(o["n"]))
                    for o in menu.get("options") or [] if isinstance(o, dict) and o.get("n") not in (None, "")]
        return _option_actions(menu)                    # a headless "Allow X?" card answers through the ledger
    if kind == "human_task":
        return [_a("done", "Done", True), _a("cant", "Can't", True, "required"), _a("snooze", "Snooze 2 h", True)]
    if kind == "questionnaire":
        return [_a("open", "Answer", True)]
    if kind == "done":
        acts = [_a("seen", "Got it", True)]
        if linked_pending:
            acts.append(_a("respond", "Reply", True, "required"))
        return acts
    if kind in ("failed", "stuck"):
        return [_a("open_agent", "Open chat", False), _a("seen", "Got it", True)]
    if kind == "missed_call":
        return [_a("seen", "Got it", True)]
    if kind == "on_behalf":
        return [_a("seen", "Got it", True), _a("open_agent", "Follow up in chat", False)]
    return []


def _push(item, ctx):
    p = ctx.push_fn(item, ctx.prefs, ctx.now, ctx.tz)
    return attention_push.apply_reminder(p, ctx.reminders.get(item["id"]), ctx.prefs, item.get("agent"),
                                         ctx.now, ctx.tz)


def _seen_at(item_id, ctx):
    s = ctx.seen.get(item_id)
    return s[0] if s else None


def _waiting(row, kind, ctx, *, blocking, urgency, agent=None):
    it = {
        "id": row["id"], "tab": "waiting", "kind": kind, "agent": agent or row.get("from_agent"),
        "created_at": row.get("created_at"), "blocking": blocking, "risk": row.get("risk_level"),
        "urgency": urgency, "actions": actions_for(kind, row), "state": "waiting",
        "answered_by": None, "answer_surface": None, "answered_at": None,
        "seen_at": _seen_at(row["id"], ctx), "push": None,
        "title": row.get("question") or row.get("title"), "summary": row.get("summary"),
        "source_id": row["id"], "priority_score": row.get("priority_score"), "card": row,
    }
    it["push"] = _push(it, ctx)
    return it


def from_ledger(row, ctx):
    kind = kind_of(row)
    if kind is None:
        return None
    return _waiting(row, kind, ctx, blocking=_blocking(row, kind, ctx), urgency=_int_or_none(row.get("urgency")))


def from_questionnaire(row, ctx):
    urg = row.get("urgency") if "urgency" in row else ctx.urgency.get(row["id"])
    return _waiting(row, "questionnaire", ctx, blocking=False, urgency=_int_or_none(urg))


def from_permission(row, ctx):
    return _waiting(row, "permission", ctx, blocking=True, urgency=_int_or_none(row.get("urgency")),
                    agent=row.get("from_agent") or row.get("session"))


def from_pending_row(row, ctx):
    """One /pending-approvals row (ledger card, questionnaire or permission pseudo-row) -> item, or None."""
    k = kind_of(row)
    if k is None:
        return None
    if k == "questionnaire":
        return from_questionnaire(row, ctx)
    if str(row.get("id", "")).startswith("perm:"):
        return from_permission(row, ctx)
    return from_ledger(row, ctx)


def waiting_items(ledger_rows, qnr_rows, perm_rows, ctx):
    items = [from_ledger(r, ctx) for r in ledger_rows]
    items += [from_questionnaire(r, ctx) for r in qnr_rows]
    items += [from_permission(r, ctx) for r in perm_rows]
    return order_waiting([i for i in items if i is not None])


def from_event(ev, ctx):
    kind = ev["kind"]
    linked = ctx.cards.get(ev.get("source_id")) if kind == "done" and ev.get("source_id") else None
    linked_pending = bool(linked) and linked.get("status") == "pending"
    seen = _seen_at(ev["id"], ctx)
    if seen is None and linked and not linked_pending:
        seen = linked.get("answered_at")                 # answering the linked card on the phone counts
    evidence = _parse(ev.get("evidence"))
    it = {
        "id": ev["id"], "tab": "update", "kind": kind, "agent": ev.get("agent"),
        "created_at": ev.get("created_at"), "blocking": False, "risk": None, "urgency": None,
        "actions": actions_for(kind, linked_pending=linked_pending),
        "state": "resolved" if ev.get("resolved_at") else "waiting",
        "answered_by": None, "answer_surface": None, "answered_at": None,
        "seen_at": seen, "push": None,
        "title": ev.get("title"), "summary": ev.get("body"), "source_id": ev.get("source_id"),
        "reason": ev.get("reason"), "occurrences": ev.get("occurrences", 1), "rule_id": ev.get("rule_id"),
        "resolved_at": ev.get("resolved_at"), "reopened_at": ev.get("reopened_at"),
        "updated_at": ev.get("updated_at"),
    }
    if kind == "done":
        it["evidence"] = evidence
    if kind == "on_behalf":
        it["on_behalf"] = evidence if isinstance(evidence, dict) else None
    it["push"] = ctx.push_fn(it, ctx.prefs, ctx.now, ctx.tz)
    po = _parse(ev.get("push_override"))
    if isinstance(po, dict) and po.get("decision"):
        it["push"] = {"decision": po["decision"], "reason": po.get("reason")}   # a rule asked for this exactly
    elif isinstance(po, dict) and isinstance(po.get("reminder"), dict):
        it["push"] = attention_push.apply_reminder(it["push"], po["reminder"], ctx.prefs, it["agent"],
                                                   ctx.now, ctx.tz)
    else:
        it["push"] = attention_push.apply_reminder(it["push"], ctx.reminders.get(ev["id"]), ctx.prefs,
                                                   it["agent"], ctx.now, ctx.tz)
    return it


def _state_of_ended(status) -> str:
    if status == "retired":
        return "resolved"
    if status == "expired":
        return "expired"
    return "answered"


def _answer_label(row):
    menu = _menu(row)
    n = row.get("option_n")
    if not menu or n in (None, ""):
        return None
    for o in menu.get("options") or []:
        if isinstance(o, dict) and str(o.get("n")) == str(n):
            return o.get("label")
    return None


def from_history(row, ctx):
    is_qnr = str(row.get("id", "")).startswith("qnr_") or row.get("kind") == "questionnaire"
    kind = "questionnaire" if is_qnr else kind_of(row)
    if kind is None:
        return None
    status = row.get("status")
    by = row.get("answered_by")
    by = ctx.person if by == "operator" else (by or None)
    surface = row.get("answer_surface")
    surface = surface if surface in SURFACES else None
    retired = row.get("retired") if isinstance(row.get("retired"), dict) else {}
    answered_at = (row.get("answered_at") or row.get("submitted_at") or row.get("discarded_at")
                   or row.get("retired_at") or retired.get("at"))
    it = {
        "id": row["id"], "tab": "answered", "kind": kind, "agent": row.get("from_agent"),
        "created_at": row.get("created_at"), "blocking": False if is_qnr else _blocking(row, kind, ctx),
        "risk": row.get("risk_level"), "urgency": _int_or_none(row.get("urgency")),
        "actions": [], "state": _state_of_ended(status),
        "answered_by": by, "answer_surface": surface, "answered_at": answered_at,
        "seen_at": _seen_at(row["id"], ctx) or answered_at, "push": None,
        "title": row.get("question") or row.get("title"), "summary": row.get("summary"),
        "outcome": status if status in ("discarded", "retired", "expired") else "answered",
        "answer": row.get("answer"), "answer_label": _answer_label(row), "answer_text": row.get("answer_text"),
        "answer_device": _device_label(row.get("answer_device")), "on_behalf": ctx.on_behalf.get(row["id"]),
    }
    return it


def _device_label(stored):
    """The ledger stores "<device_id>:<label>"; /attention shows only the label, so a scoped member never
    sees the id of the person's paired device."""
    if not isinstance(stored, str) or not stored:
        return None
    return stored.split(":", 1)[1] if ":" in stored else stored


def order_waiting(items):
    """Blocking first, then newest first (the spec). Two stable sorts: created_at is an ISO string."""
    items = sorted(items, key=lambda i: i.get("created_at") or "", reverse=True)
    return sorted(items, key=lambda i: not i.get("blocking"))


def order_updates(items):
    items = sorted(items, key=lambda i: i.get("reopened_at") or i.get("created_at") or "", reverse=True)
    return sorted(items, key=lambda i: i.get("state") != "waiting")


def order_answered(items):
    return sorted(items, key=lambda i: i.get("answered_at") or "", reverse=True)


def counts(items):
    waiting = [i for i in items if i["tab"] == "waiting"]
    return {"waiting": len(waiting), "waiting_blocking": sum(1 for i in waiting if i["blocking"]),
            "updates_unread": sum(1 for i in items if i["tab"] == "update" and not i["seen_at"])}
