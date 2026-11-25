"""Dismiss works for EVERY card kind and says who, where and why (operator order 2026-11-25, gm msg_b083c465).

Shaw pressed Deny on a task-for-you card on the web and got a bare 400; there was no Dismiss. The gateway's discard
(pending -> 'discarded', history, one honest notify to the author) already took any pending row; it now also keeps a
history line on the card's summary naming the surface, the answerer and the reason (as retire does), stamps the
attribution columns when they are armed, and the author's notice carries the reason. No new columns: the line lives in
`summary`, so an unarmed install behaves the same.

Hermetic: tmp tasks.db, HOME a tmp dir, no ntfy; the author's notice is captured by stubbing the message store.
"""
import os

import pytest
import approval_resume
from approval_schema import ApprovalStore


@pytest.fixture(autouse=True)
def fenced(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NTFY_TOKEN_FILE", "/nonexistent/ntfy-token-for-tests")
    monkeypatch.delenv("APPROVAL_DDL_ARMED", raising=False)


@pytest.fixture
def store(tmp_path):
    s = ApprovalStore(db_path=str(tmp_path / "tasks.db")); s.migrate(); return s


def _approval(s):
    return s.create(from_agent="pm-demo", question="Ship it?", worker_kind="pane", options=["approve", "deny", "hold"])


def _task(s):
    return s.create(from_agent="postcall-dev", question="Call Dana back", worker_kind="pane",
                    options=["done", "cant", "snooze"], kind="human_task", block_task="Call Dana back")


def _menu(s):
    return s.create(from_agent="codex-gm", question="Which build?", worker_kind="pane", kind="menu",
                    op_key="menu:codex-gm:abc", menu={"options": [{"n": "1", "label": "A"}, {"n": "2", "label": "B"}]},
                    origin="menu_bridge")


@pytest.mark.parametrize("make", [_approval, _task, _menu], ids=["approval", "human_task", "menu"])
def test_every_kind_is_dismissed_with_a_history_line(store, make):
    rid = make(store)
    assert store.mark_discarded(rid, surface="web", by="operator", reason="old, remove") is True
    row = store.get(rid)
    assert row["status"] == "discarded" and row["discarded_at"]
    assert "DISMISSED" in (row["summary"] or "") and "via web" in row["summary"] and "old, remove" in row["summary"]


def test_a_dismiss_without_a_reason_still_leaves_a_line(store):
    rid = _task(store)
    assert store.mark_discarded(rid, surface="phone") is True
    assert "DISMISSED" in store.get(rid)["summary"] and "via phone" in store.get(rid)["summary"]


def test_the_plain_call_still_works_and_only_a_pending_row_is_dismissed(store):
    rid = _task(store)
    assert store.mark_discarded(rid) is True            # the gateway's existing call shape: no line, same as before
    assert "DISMISSED" not in (store.get(rid)["summary"] or "")
    assert store.mark_discarded(rid, reason="again") is False
    assert "again" not in (store.get(rid)["summary"] or "")


def test_attribution_columns_are_stamped_when_armed(store, monkeypatch, tmp_path):
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "m20260825_answer_attribution")
    s = ApprovalStore(db_path=str(tmp_path / "armed.db")); s.migrate()
    rid = _task(s)
    assert s.mark_discarded(rid, surface="watch", by="operator", reason="done another way") is True
    row = s.get(rid)
    assert row.get("answer_surface") == "watch" and row.get("answered_by") == "operator"


def test_the_author_is_told_the_reason(store, monkeypatch):
    rid = _task(store)
    store.mark_discarded(rid, surface="web", by="operator", reason="old, remove")
    sent = {}

    class Msgs:
        def send(self, **kw): sent.update(kw)
    monkeypatch.setattr(approval_resume, "_msg_store", lambda: Msgs())
    approval_resume.deliver_discarded(store.get(rid), store)
    assert sent["to_agent"] == "postcall-dev" and sent["type"] == "approval_discarded"
    assert "DISMISSED" in sent["body"] and "old, remove" in sent["body"] and "web" in sent["body"]
    assert "notify" not in sent["type"]            # told by mail, never a push


def test_gateway_discard_fields_are_strings_trimmed_and_capped():
    from watch_gateway import discard_fields
    assert discard_fields(None) == {"surface": None, "by": None, "reason": None}
    assert discard_fields({"surface": " web ", "reason": "  old  ", "by": "operator"}) == {
        "surface": "web", "by": "operator", "reason": "old"}
    assert discard_fields({"surface": 7, "reason": ["x"], "by": {}}) == {"surface": None, "by": None, "reason": None}
    assert len(discard_fields({"reason": "r" * 5000})["reason"]) == 500
    assert discard_fields({"surface": "nope"})["surface"] is None     # only the known surfaces
