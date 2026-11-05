"""A questionnaire may SKIP any question, required or not, with the skip sent explicitly.

The shared question card sends a question the person chose to leave unanswered as
{"skipped": true}. These tests run the REAL gateway route (POST /questionnaires/{id}/submit on
build_app) over a real QuestionnaireStore on a temp database, and read back what was stored and
what the asking agent is told (questionnaire_resume.digest).
"""
import asyncio
import json
import os
import sys

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watch_gateway as G  # noqa: E402
import questionnaire_resume as QR  # noqa: E402
from questionnaire_schema import QuestionnaireStore  # noqa: E402

FLEET = "fleet-bearer-0123456789abcdef-for-tests"

QUESTIONS = [
    {"prompt": "Which colour?", "kind": "menu",
     "menu": {"options": [{"n": "1", "label": "Blue"}, {"n": "2", "label": "Green"}]}},
    {"prompt": "Any notes?", "kind": "free_text", "optional": True},
    {"prompt": "Which finish?", "kind": "menu", "optional": True,
     "menu": {"options": [{"n": "1", "label": "Matte"}, {"n": "2", "label": "Gloss"}]}},
]


def _gateway_auth(mp, tmp_path):
    tok = tmp_path / "gateway-token"
    tok.write_text(FLEET)
    mp.setattr(G, "TOKEN_FILE", tok)


@pytest.fixture
def qnr(tmp_path, monkeypatch):
    _gateway_auth(monkeypatch, tmp_path)
    db = str(tmp_path / "t.db")
    store = QuestionnaireStore(db_path=db)
    store.migrate()
    qid = store.create(from_agent="dev-demo", title="Sample choices", questions=QUESTIONS)
    monkeypatch.setattr(G, "QuestionnaireStore", lambda: QuestionnaireStore(db_path=db))
    delivered = []
    monkeypatch.setattr(G.questionnaire_resume, "deliver", lambda row, store: delivered.append(row))
    return {"store": store, "id": qid, "delivered": delivered}


def _submit(qnr, answers):
    async def go():
        async with TestClient(TestServer(G.build_app())) as c:
            r = await c.post(f"/questionnaires/{qnr['id']}/submit", json={"answers": answers, "surface": "web"},
                             headers={"Authorization": f"Bearer {FLEET}"})
            return r.status, await r.json()
    return asyncio.run(go())


def _answers(qnr):
    return {q["n"]: q["answer"] for q in qnr["store"].get(qnr["id"])["questions"]}


def test_all_answered_submits(qnr):
    st, body = _submit(qnr, {"1": {"option_n": "2"}, "2": {"answer_text": "soon"}, "3": {"option_n": "1"}})
    assert st == 200 and body["applied"] is True, body
    a = _answers(qnr)
    assert a[1]["option_n"] == "2" and a[2]["answer_text"] == "soon" and a[3]["state"] == "final"
    assert len(qnr["delivered"]) == 1


def test_skipped_optional_questions_are_recorded_as_skipped(qnr):
    st, body = _submit(qnr, {"1": {"option_n": "1"}, "2": {"skipped": True}, "3": {"skipped": True}})
    assert st == 200 and body["applied"] is True, body
    a = _answers(qnr)
    assert a[1]["state"] == "final"
    assert a[2] == {"option_n": None, "answer_text": None, "state": "skipped"}
    assert a[3]["state"] == "skipped"
    text = QR.digest(qnr["store"].get(qnr["id"]))
    assert "2. Any notes? → skipped" in text and "3. Which finish? → skipped" in text
    machine = json.loads(text.split("```json\n")[1].split("\n```")[0])
    assert machine["answers"][1]["skipped"] is True and "skipped" not in machine["answers"][0]


def test_a_required_question_can_be_skipped(qnr):
    """A questionnaire is accepted without every question answered. A question
    marked required is skipped like any other when the person moves past it."""
    st, body = _submit(qnr, {"1": {"skipped": True}, "2": {"skipped": True}, "3": {"option_n": "1"}})
    assert st == 200 and body["applied"] is True, body
    a = _answers(qnr)
    assert a[1] == {"option_n": None, "answer_text": None, "state": "skipped"}
    assert a[3]["state"] == "final"
    assert "1. Which colour? \u2192 skipped" in QR.digest(qnr["store"].get(qnr["id"]))
    assert len(qnr["delivered"]) == 1


def test_a_required_question_simply_left_out_still_blocks(qnr):
    """An app that leaves a required question OUT (neither answered nor skipped) is told which one
    is missing, as before: only an explicit skip passes it."""
    st, body = _submit(qnr, {"3": {"option_n": "1"}})
    assert st == 200 and body["applied"] is False and body["missing"] == [1], body
    assert qnr["store"].get(qnr["id"])["status"] == "pending"


def test_every_question_skipped_is_accepted_and_delivered(qnr):
    """Submit with nothing answered (the card asks once first): every question is recorded as
    skipped and the asking agent is told so."""
    st, body = _submit(qnr, {"1": {"skipped": True}, "2": {"skipped": True}, "3": {"skipped": True}})
    assert st == 200 and body["applied"] is True, body
    row = qnr["store"].get(qnr["id"])
    assert row["status"] == "submitted"
    assert all(q["answer"]["state"] == "skipped" for q in row["questions"])
    assert len(qnr["delivered"]) == 1


def test_nothing_sent_at_all_is_refused_and_nothing_kept(tmp_path, monkeypatch):
    """Every question optional and NOTHING sent (no answers, no skips): refused as before."""
    _gateway_auth(monkeypatch, tmp_path)
    db = str(tmp_path / "all-optional.db")
    store = QuestionnaireStore(db_path=db); store.migrate()
    qid = store.create(from_agent="dev-demo", title="All optional",
                       questions=[{**q, "optional": True} for q in QUESTIONS])
    monkeypatch.setattr(G, "QuestionnaireStore", lambda: QuestionnaireStore(db_path=db))
    st, body = _submit({"id": qid}, {})
    assert st == 400 and body["error"] == "nothing answered: answer at least one question"
    st, body = _submit({"id": qid}, {"1": {"skipped": True}})
    assert st == 400 and body["error"] == "nothing answered: answer at least one question"
    row = store.get(qid)
    assert row["status"] == "pending" and all(q["answer"] is None for q in row["questions"])


def test_a_skip_carrying_an_answer_is_refused(qnr):
    st, body = _submit(qnr, {"1": {"option_n": "1"}, "3": {"skipped": True, "option_n": "1"}})
    assert st == 400 and body["error"] == "q3: a skipped question cannot carry an answer"
    st, body = _submit(qnr, {"1": {"option_n": "1"}, "2": {"skipped": True, "answer_text": "x"}})
    assert st == 400 and body["error"] == "q2: a skipped question cannot carry an answer"
    st, body = _submit(qnr, {"1": {"option_n": "1"}, "2": {"skipped": False}})
    assert st == 400 and body["error"] == "q2: skipped must be true"


def test_a_skip_is_not_a_draft(qnr):
    assert G._validate_qnr_delta(qnr["store"].get(qnr["id"])["questions"], {"2": {"skipped": True}}) \
        == "q2: a skip is sent with the submit, not a draft"


def test_a_question_simply_left_out_still_works_as_before(qnr):
    """Apps that send only what was answered keep working: an optional question left out is just
    unanswered (no 'skipped' state), exactly as before skips existed."""
    st, body = _submit(qnr, {"1": {"option_n": "1"}})
    assert st == 200 and body["applied"] is True
    assert _answers(qnr)[2] is None


def test_a_skip_overrides_an_earlier_draft(qnr):
    qnr["store"].save_draft(qnr["id"], {"3": {"option_n": "2"}})
    st, body = _submit(qnr, {"1": {"option_n": "1"}, "3": {"skipped": True}})
    assert st == 200 and body["applied"] is True
    assert _answers(qnr)[3] == {"option_n": None, "answer_text": None, "state": "skipped"}
