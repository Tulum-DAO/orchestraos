import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attention_push as P  # noqa: E402

NOON = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("prefs,kind,tab,want", [
    ({}, "decision", "waiting", "push"),                                     # all (default)
    ({"notify_level": "needs_me"}, "done", "update", "list"),
    ({"notify_level": "needs_me"}, "permission", "waiting", "push"),
    ({"notify_level": "nothing"}, "failed", "update", "list"),
    ({"quiet_hours": {"from": "11:00", "to": "13:00"}}, "decision", "waiting", "held"),
    ({"notify_agents": {"gm": "nothing"}}, "decision", "waiting", "list"),
    ({"notify_level": "needs_me"}, "stuck", "update", "list"),               # stuck is notify_policy's "failed"
    ({}, "missed_call", "update", "push"),
])
def test_today_settings_drive_the_decision(prefs, kind, tab, want):
    item = {"kind": kind, "tab": tab, "agent": "gm-gen3", "blocking": kind != "done", "risk": None}
    assert P.decide_push(item, prefs, NOON, "UTC")["decision"] == want


def test_reasons_say_why():
    item = {"kind": "decision", "tab": "waiting", "agent": "gm", "blocking": True, "risk": None}
    assert P.decide_push(item, {"notify_level": "needs_me"}, NOON, "UTC")["reason"] == "notify_level=needs_me"
    q = {"quiet_hours": {"from": "11:00", "to": "13:00"}}
    assert P.decide_push(item, q, NOON, "UTC")["reason"] == "quiet_hours"


def test_answered_items_have_no_push():
    assert P.decide_push({"tab": "answered", "kind": "decision", "agent": "gm"}, {}, NOON, "UTC") is None


@pytest.mark.parametrize("level,item,want", [
    ("let_them_run", {"kind": "done", "tab": "update", "blocking": False, "risk": None}, "list"),
    ("let_them_run", {"kind": "failed", "tab": "update", "blocking": False, "risk": None}, "push"),
    ("let_them_run", {"kind": "decision", "tab": "waiting", "blocking": True, "risk": None}, "push"),
    ("let_them_run", {"kind": "questionnaire", "tab": "waiting", "blocking": False, "risk": "high"}, "push"),
    ("let_them_run", {"kind": "questionnaire", "tab": "waiting", "blocking": False, "risk": None}, "list"),
    ("check_in", {"kind": "done", "tab": "update", "blocking": False, "risk": None}, "list"),
    ("check_in", {"kind": "questionnaire", "tab": "waiting", "blocking": False, "risk": None}, "push"),
    ("watch_closely", {"kind": "done", "tab": "update", "blocking": False, "risk": None}, "push"),
])
def test_part2_levels_are_pure_and_ready(level, item, want):
    item = {**item, "agent": "gm"}
    assert P.decide_push(item, {}, NOON, "UTC", level=level)["decision"] == want


def test_agent_level_wins_and_quiet_hours_hold_a_level_push():
    item = {"kind": "done", "tab": "update", "agent": "gm", "blocking": False, "risk": None}
    got = P.decide_push(item, {}, NOON, "UTC", level="check_in", agent_level="watch_closely")
    assert got == {"decision": "push", "reason": "agent_level=watch_closely"}
    q = {"quiet_hours": {"from": "11:00", "to": "13:00"}}
    assert P.decide_push(item, q, NOON, "UTC", level="watch_closely")["decision"] == "held"


def test_unknown_level_raises():
    with pytest.raises(ValueError):
        P.decide_push({"kind": "done", "tab": "update", "agent": "gm"}, {}, NOON, "UTC", level="yolo")


def test_a_reminder_raises_list_to_push_but_never_breaks_the_persons_mute():
    r = {"rule_id": "rul_1", "n": 2}
    base = {"decision": "list", "reason": "notify_level=needs_me"}
    assert P.apply_reminder(base, r, {}, "gm", NOON, "UTC") == {
        "decision": "push", "reason": "reminder:rul_1", "reminder_n": 2}
    q = {"quiet_hours": {"from": "11:00", "to": "13:00"}}
    assert P.apply_reminder(base, r, q, "gm", NOON, "UTC")["decision"] == "held"
    mute = {"notify_agents": {"gm": "nothing"}}
    assert P.apply_reminder(base, r, mute, "gm-gen2", NOON, "UTC")["decision"] == "list"
    assert P.apply_reminder(None, r, {}, "gm", NOON, "UTC") is None          # answered: nothing to raise
    assert P.apply_reminder(base, None, {}, "gm", NOON, "UTC") == base
