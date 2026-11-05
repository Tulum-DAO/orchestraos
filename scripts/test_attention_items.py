import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attention_items as I  # noqa: E402

T = "2026-11-04T09:00:00+00:00"
T2 = "2026-11-04T09:30:00+00:00"
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
SPEC = {"id", "tab", "kind", "agent", "created_at", "blocking", "risk", "urgency", "actions", "state",
        "answered_by", "answer_surface", "answered_at", "seen_at", "push"}


@pytest.fixture
def ctx():
    return I.Ctx(person="alex", prefs={}, tz="UTC", now=NOW)


def mk(i, blocking, created):
    return {"id": i, "blocking": blocking, "created_at": created}


def menu_row(**over):
    menu = {"question": "Pick", "options": [{"n": "1", "label": "A", "input_kind": "direct"},
                                            {"n": "2", "label": "Type something", "input_kind": "free_text"},
                                            {"n": "3", "label": "Chat about this", "input_kind": "chat"}]}
    menu.update(over.pop("menu", {}))
    return {"id": "apr_m", "kind": "menu", "from_agent": "gm", "created_at": T, "question": "Pick",
            "menu": menu, **over}


def sample_items(ctx):
    c = ctx.with_cards({"apr_7": {"status": "pending"}})
    return [
        I.from_ledger({"id": "apr_1", "kind": None, "from_agent": "gm", "created_at": T, "question": "Q?"}, c),
        I.from_ledger(menu_row(), c),
        I.from_ledger(menu_row(id="apr_mm", menu={"checkbox": True}), c),
        I.from_ledger({"id": "apr_3", "kind": "human_task", "from_agent": "gm", "created_at": T,
                       "question": "Plug in", "blocks_what": "deploy", "feature": None}, c),
        I.from_questionnaire({"id": "qnr_1", "kind": "questionnaire", "from_agent": "gm", "created_at": T,
                              "question": "t"}, c),
        I.from_permission({"id": "perm:gm:abc:1", "kind": "permission", "from_agent": "gm", "session": "gm",
                           "created_at": T, "question": "Allow Bash?", "urgency": 2,
                           "menu": {"options": [{"n": "1", "label": "Yes"}, {"n": "3", "label": "No"}]}}, c),
        I.from_event({"id": "evt_1", "kind": "done", "agent": "gm", "created_at": T, "resolved_at": T,
                      "title": "Shipped", "source_id": "apr_7"}, c),
        I.from_event({"id": "evt_2", "kind": "stuck", "agent": "qa", "created_at": T, "resolved_at": None,
                      "title": "qa stuck", "reason": "no_progress"}, c),
        I.from_event({"id": "evt_3", "kind": "failed", "agent": "qa", "created_at": T, "resolved_at": None,
                      "title": "qa crashed", "reason": "crashed"}, c),
        I.from_event({"id": "evt_4", "kind": "missed_call", "agent": "gm", "created_at": T, "resolved_at": T,
                      "title": "missed"}, c),
        I.from_event({"id": "evt_5", "kind": "on_behalf", "agent": "gm", "created_at": T, "resolved_at": T,
                      "title": "closed for you", "evidence": json.dumps({"by": "rule:rul_1", "action": "expire"})}, c),
        I.from_history({"id": "apr_5", "status": "answered", "from_agent": "gm", "created_at": T,
                        "answered_at": T2, "answered_by": "operator", "answer_surface": "watch",
                        "question": "Q"}, c),
    ]


def test_kinds_and_blocking(ctx):
    assert I.from_ledger({"id": "apr_1", "kind": None, "from_agent": "gm", "created_at": T, "risk_level": "high",
                          "question": "Q?"}, ctx)["kind"] == "decision"
    perm_menu = {"id": "apr_2", "kind": "menu", "from_agent": "gm", "created_at": T, "question": "Allow X?",
                 "menu": {"bridge": {"permission": True}, "options": [{"n": "1", "label": "Allow"}]}}
    assert I.from_ledger(perm_menu, ctx)["kind"] == "permission"
    assert I.from_ledger({**perm_menu, "menu": json.dumps(perm_menu["menu"])}, ctx)["kind"] == "permission"
    task = {"id": "apr_3", "kind": "human_task", "from_agent": "gm", "created_at": T, "question": "Plug in",
            "blocks_what": "deploy", "feature": None}
    com = {**task, "id": "apr_4", "feature": "commitment"}
    assert I.from_ledger(task, ctx)["blocking"] is True and I.from_ledger(com, ctx)["blocking"] is False
    assert I.from_ledger({**task, "blocks_what": None}, ctx)["blocking"] is False
    assert I.from_questionnaire({"id": "qnr_1", "kind": "questionnaire", "from_agent": "gm", "created_at": T,
                                 "question": "t"}, ctx)["blocking"] is False
    assert I.from_ledger(menu_row(), ctx)["blocking"] is True


def test_plain_approval_blocks_only_while_its_agent_is_not_working(ctx):
    row = {"id": "apr_1", "kind": None, "from_agent": "gm-gen2", "created_at": T, "question": "Q?"}
    working = I.Ctx(person="alex", prefs={}, tz="UTC", now=NOW, agent_states={"gm-gen2": "working"})
    idle = I.Ctx(person="alex", prefs={}, tz="UTC", now=NOW, agent_states={"gm-gen2": "idle"})
    assert I.from_ledger(row, working)["blocking"] is False
    assert I.from_ledger(row, idle)["blocking"] is True
    assert I.from_ledger({**row, "kind": "proposal"}, idle)["blocking"] is False   # no agent waits on a proposal


def test_completion_rows_are_not_waiting_items(ctx):
    assert I.waiting_items([{"id": "apr_9", "kind": "completion", "from_agent": "gm", "created_at": T,
                             "question": "x"}], [], [], ctx) == []


def test_order_blocking_first_then_newest():
    items = [mk("a", False, "2026-11-04T10:00"), mk("b", True, "2026-11-04T09:00"), mk("c", True, "2026-11-04T09:30")]
    assert [i["id"] for i in I.order_waiting(items)] == ["c", "b", "a"]


def test_answered_fields_normalised(ctx):
    row = {"id": "apr_5", "status": "answered", "from_agent": "gm", "created_at": T, "answered_at": T2,
           "answered_by": "operator", "answer_surface": "agent_cli", "question": "Q"}
    it = I.from_history(row, ctx)
    assert it["tab"] == "answered" and it["state"] == "answered"
    assert it["answered_by"] == "alex" and it["answer_surface"] is None and it["push"] is None
    assert I.from_history({**row, "status": "retired", "retired": {"at": T2}}, ctx)["state"] == "resolved"
    assert I.from_history({**row, "status": "expired"}, ctx)["state"] == "expired"
    assert I.from_history({**row, "status": "discarded"}, ctx)["outcome"] == "discarded"
    assert I.from_history({**row, "answered_by": "rule:rul_1", "answer_surface": "rule"}, ctx)[
        "answer_surface"] == "rule"
    assert I.from_history({**row, "answered_by": "dev-1"}, ctx)["answered_by"] == "dev-1"
    assert I.from_history({**row, "answer_device": "dev_1:Phone"}, ctx)["answer_device"] == "dev_1:Phone"


def test_answered_menu_carries_the_label_and_questionnaires_map_too(ctx):
    row = menu_row(status="resumed", answered_at=T2, answer="option", option_n="1", answered_by="operator",
                   answer_surface="web")
    it = I.from_history(row, ctx)
    assert it["answer_label"] == "A" and it["kind"] == "decision"
    q = {"id": "qnr_2", "from_agent": "gm", "title": "Prefs", "created_at": T, "submitted_at": T2,
         "status": "submitted"}
    qi = I.from_history(q, ctx)
    assert (qi["kind"], qi["state"], qi["answered_at"]) == ("questionnaire", "answered", T2)


def test_done_seen_from_linked_card(ctx):
    ev = {"id": "evt_1", "kind": "done", "agent": "gm", "created_at": T, "resolved_at": T, "title": "Shipped",
          "source_id": "apr_7"}
    ctx2 = ctx.with_cards({"apr_7": {"status": "answered", "answered_at": T2, "answer_surface": "watch"}})
    it = I.from_event(ev, ctx2)
    assert it["seen_at"] == T2 and [a["type"] for a in it["actions"]] == ["seen"]
    ctx3 = ctx.with_cards({"apr_7": {"status": "pending"}})
    assert [a["type"] for a in I.from_event(ev, ctx3)["actions"]] == ["seen", "respond"]
    assert I.from_event(ev, ctx3)["state"] == "resolved"


def test_actions_per_kind(ctx):
    by = {it["id"]: it for it in sample_items(ctx)}
    assert [a["type"] for a in by["apr_1"]["actions"]] == ["approve", "deny", "respond"]
    m = by["apr_m"]["actions"]
    assert [(a["type"], a["n"], a["needs_text"], a["watch_ok"]) for a in m] == [
        ("option", "1", "none", True), ("option", "2", "required", True), ("option", "3", "none", False)]
    assert [a["type"] for a in by["apr_mm"]["actions"]] == ["submit"]
    assert [a["type"] for a in by["apr_3"]["actions"]] == ["done", "cant", "snooze"]
    assert [a["type"] for a in by["qnr_1"]["actions"]] == ["open"]
    assert [(a["type"], a["n"]) for a in by["perm:gm:abc:1"]["actions"]] == [("key", "1"), ("key", "3")]
    assert [a["type"] for a in by["evt_2"]["actions"]] == ["open_agent", "seen"]
    assert [a["type"] for a in by["evt_4"]["actions"]] == ["seen"]
    assert [a["type"] for a in by["evt_5"]["actions"]] == ["seen", "open_agent"]
    assert by["evt_5"]["on_behalf"]["action"] == "expire"
    terminal = I.from_permission({"id": "perm:gm:x:1", "kind": "permission", "from_agent": "gm", "created_at": T,
                                  "question": "?", "answer_in_terminal": True, "menu": {"options": []}}, ctx)
    assert [(a["type"], a["watch_ok"]) for a in terminal["actions"]] == [("open_agent", False)]


def test_event_state_and_push(ctx):
    by = {it["id"]: it for it in sample_items(ctx)}
    assert by["evt_2"]["state"] == "waiting" and by["evt_2"]["tab"] == "update"
    assert by["evt_2"]["push"]["decision"] == "push"            # default prefs: All
    assert by["apr_5"]["push"] is None
    quiet = I.from_event({"id": "evt_9", "kind": "stuck", "agent": "qa", "created_at": T, "resolved_at": None,
                          "title": "x", "push_override": json.dumps({"decision": "list", "reason": "rule:rul_1"})},
                         ctx)
    assert quiet["push"] == {"decision": "list", "reason": "rule:rul_1"}
    nudged = I.from_event({"id": "evt_8", "kind": "stuck", "agent": "qa", "created_at": T, "resolved_at": None,
                           "title": "x", "push_override": json.dumps({"reminder": {"rule_id": "rul_2", "n": 1}})},
                          I.Ctx(person="alex", prefs={"notify_level": "needs_me"}, tz="UTC", now=NOW))
    assert nudged["push"]["reason"] == "reminder:rul_2"


def test_a_reminder_on_a_waiting_card_raises_its_push():
    c = I.Ctx(person="alex", prefs={"notify_agents": {"gm": "needs_me"}}, tz="UTC", now=NOW,
              reminders={"apr_1": {"rule_id": "rul_1", "n": 3}})
    it = I.from_ledger({"id": "apr_1", "kind": None, "from_agent": "gm", "created_at": T, "question": "Q?"}, c)
    assert it["push"] == {"decision": "push", "reason": "reminder:rul_1", "reminder_n": 3}


def test_seen_map_and_urgency(ctx):
    c = I.Ctx(person="alex", prefs={}, tz="UTC", now=NOW, seen={"apr_1": (T2, "watch")}, urgency={"qnr_1": 3})
    assert I.from_ledger({"id": "apr_1", "kind": None, "from_agent": "gm", "created_at": T, "question": "Q?",
                          "urgency": 2}, c)["seen_at"] == T2
    assert I.from_ledger({"id": "apr_1", "kind": None, "from_agent": "gm", "created_at": T, "question": "Q?",
                          "urgency": 2}, c)["urgency"] == 2
    assert I.from_ledger({"id": "apr_2", "kind": None, "from_agent": "gm", "created_at": T,
                          "question": "Q?"}, c)["urgency"] is None
    assert I.from_questionnaire({"id": "qnr_1", "from_agent": "gm", "created_at": T, "question": "t"}, c)[
        "urgency"] == 3


def test_waiting_card_keeps_the_feed_row_unchanged(ctx):
    row = {"id": "apr_1", "kind": None, "from_agent": "gm", "created_at": T, "question": "Q?", "priority_score": 7}
    it = I.from_ledger(row, ctx)
    assert it["card"] is row and it["priority_score"] == 7 and it["title"] == "Q?"


def test_counts(ctx):
    items = sample_items(ctx)
    c = I.counts(items)
    assert c["waiting"] == 6 and c["waiting_blocking"] == 5 and c["updates_unread"] == 5


def test_every_item_has_exactly_the_spec_fields(ctx):
    for it in sample_items(ctx):
        assert SPEC <= set(it)
        for a in it["actions"]:
            assert {"type", "label", "watch_ok", "needs_text"} <= set(a)


def test_from_pending_row_dispatches_on_shape(ctx):
    assert I.from_pending_row({"id": "qnr_1", "kind": "questionnaire", "from_agent": "gm", "created_at": T,
                               "question": "t"}, ctx)["kind"] == "questionnaire"
    assert I.from_pending_row({"id": "perm:gm:a:1", "kind": "permission", "from_agent": "gm", "created_at": T,
                               "question": "?", "menu": {"options": []}}, ctx)["kind"] == "permission"
    assert I.from_pending_row({"id": "apr_9", "kind": "completion", "from_agent": "gm", "created_at": T,
                               "question": "x"}, ctx) is None
