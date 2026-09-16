"""ntfy push for kind=menu rows carries NO action buttons (orchestra-builder
ruling msg_0318716b, option 1; found while refuting msg_69fceffd).

build_payload gave every row 3 http action buttons whose body is the LEGACY
{id, answer:"<option LABEL>"} shape. For a menu row that is wrong in both
directions: the gateway rejects it (400 "menu rows answer via answer=='option'
only") and approval_listener.handle_event would record the label with
option_n=None and flip the row to resumed (silent mis-answer). Menu rows now
mirror the watch notification rule (ApprovalPoller.swift:223): no quick
actions — the tap opens the app to the card, where the options ARE the
actions. Non-menu rows are byte-pinned unchanged.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approval_notify as N
from approval_schema import ApprovalStore


def _store(tmp_path):
    s = ApprovalStore(db_path=str(tmp_path / "t.db")); s.migrate()
    return s


def test_menu_row_push_has_no_action_buttons(tmp_path):
    s = _store(tmp_path)
    menu = {"question": "OK to pull live reach?", "options": [
        {"n": "1", "label": "Approve both", "input_kind": "direct"},
        {"n": "2", "label": "Hold", "input_kind": "direct"},
        {"n": "3", "label": "Other / Write-in...", "input_kind": "free_text"}]}
    rid = s.create(from_agent="pm-aiordie", question="OK to pull live reach?",
                   worker_kind="pane", kind="menu", menu=menu,
                   options=["Approve both", "Hold", "Other / Write-in..."])
    p = N.build_payload(s.get(rid), token="tk")
    assert "actions" not in p or p["actions"] == []
    assert p["topic"] == N.NTFY_APPROVALS_TOPIC and p["priority"] == 4
    assert p["message"] == "OK to pull live reach?" and rid in p["title"]
    assert "tk" not in json.dumps(p)                     # no bearer leaks without buttons


def test_menu_row_push_never_carries_a_label_answer(tmp_path):
    """The exact defect: a label can never appear as an `answer` body."""
    s = _store(tmp_path)
    rid = s.create(from_agent="gm", question="Which slice?", worker_kind="pane", kind="menu",
                   menu={"question": "Which slice?", "options": [{"n": "1", "label": "Slice A"}]},
                   options=["Slice A"])
    assert "Slice A" not in json.dumps(N.build_payload(s.get(rid), token="tk").get("actions", []))


def test_non_menu_row_push_byte_identical(tmp_path):
    s = _store(tmp_path)
    rid = s.create(from_agent="worker", question="Ship it?", worker_kind="dev",
                   options=["approve", "deny", "hold"])
    row = s.get(rid)
    p = N.build_payload(row, token="tk_test")
    expected = {
        "topic": N.NTFY_APPROVALS_TOPIC,
        "title": f"Approval {rid} — worker",
        "message": "Ship it?",
        "priority": 4,
        "actions": [
            {"action": "http", "label": lbl, "url": f"{N.NTFY_BASE}/{N.NTFY_ANSWERS_TOPIC}",
             "method": "POST", "headers": {"Authorization": "Bearer tk_test"},
             "body": json.dumps({"id": rid, "answer": opt}), "clear": True}
            for opt, lbl in (("approve", "Approve"), ("deny", "Deny"), ("hold", "Hold"))
        ],
    }
    assert json.dumps(p, sort_keys=True) == json.dumps(expected, sort_keys=True)


def test_human_task_row_keeps_its_buttons(tmp_path):
    """Only kind=menu loses buttons; other kinds are untouched."""
    s = _store(tmp_path)
    rid = s.create(from_agent="gm", question="Plug in the phone", worker_kind="dev",
                   kind="human_task", options=["done", "cant", "snooze"])
    p = N.build_payload(s.get(rid), token="tk")
    assert [a["label"] for a in p["actions"]] == ["done", "cant", "snooze"]
