"""B5: answering a demo card must not try to inject anywhere. A row tagged feature='demo'
is acked by the resume beats the moment it is answered — no msg_store row, no pane
resolve, no inject, no escalation."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approval_resume as ar
import questionnaire_resume as qr
from approval_schema import ApprovalStore
from questionnaire_schema import QuestionnaireStore


def _db():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    os.environ["APPROVAL_DDL_ARMED"] = "m20260825_answer_attribution,m20260825_human_task"
    return path


def test_answered_demo_approval_is_acked_without_any_delivery(monkeypatch):
    store = ApprovalStore(db_path=_db()); store.migrate()
    rid = store.create(from_agent="demo-planner", question="Demo?", worker_kind="node",
                       options=["approve", "deny"], feature="demo", op_key="demo-t")
    store.record_answer(rid, "approve", None)
    monkeypatch.setattr(ar, "fire_resume", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not deliver")))
    ar.watchdog(store=store)
    row = store.get(rid)
    assert row["status"] == "resumed" and row["resume_acked_at"]


def test_submitted_demo_questionnaire_is_acked_without_any_delivery(monkeypatch):
    store = QuestionnaireStore(db_path=_db()); store.migrate()
    qid = store.create(from_agent="demo-reviewer", title="Demo",
                       questions=[{"prompt": "Ok?", "kind": "menu", "menu": {"options": ["yes", "no"]}}],
                       worker_kind="node", feature="demo", op_key="demo-q")
    c = store._conn()
    ns = [r[0] for r in c.execute("SELECT n FROM questionnaire_questions WHERE qnr_id=?", [qid])]
    c.close()
    res = store.submit(qid, answers={str(n): {"option_n": 1} for n in ns})
    assert res and res.get("applied"), res
    called = []
    qr.watchdog(store=store, resolve=lambda aid: called.append(aid) or (None, "x"),
                inject=lambda s, t: called.append(s) or (False, {}))
    assert called == []
    row = store.get(qid)
    assert row.get("resume_acked_at") or row.get("status") in ("resumed", "acked"), row


def test_fire_resume_itself_acks_a_demo_row_without_resolving_or_injecting(monkeypatch):
    """The api/gateway answer path calls fire_resume directly (not only the watchdog)."""
    store = ApprovalStore(db_path=_db()); store.migrate()
    rid = store.create(from_agent="demo-builder", question="Demo?", worker_kind="node",
                       options=["approve", "deny"], feature="demo", op_key="demo-f")
    store.record_answer(rid, "approve", None)
    monkeypatch.setattr(ar, "_default_resolve", lambda aid: (_ for _ in ()).throw(AssertionError("resolved")))
    monkeypatch.setattr(ar, "_default_inject", lambda s, t: (_ for _ in ()).throw(AssertionError("injected")))
    monkeypatch.setattr(ar, "_default_msg_send", lambda r, b: (_ for _ in ()).throw(AssertionError("msg sent")))
    out = ar.fire_resume(store.get(rid), store)
    assert out["reason"] == "demo-acked"
    row = store.get(rid)
    assert row["status"] == "resumed" and row["resume_acked_at"] and row["resume_attempts"] == 0
