"""`approval.py qack` after the answers were already delivered live: success, not an error.

A verified live delivery acks the questionnaire itself (questionnaire_resume.deliver -> store.ack), so the agent's own
`qack` after reading the answers used to get {"acked": false} and exit 1, telling it the opposite of what happened.
Runs the REAL CLI (a subprocess of approval.py) against a temp database through APPROVAL_DB_PATH.
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from questionnaire_schema import QuestionnaireStore  # noqa: E402


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "tasks.db")
    QuestionnaireStore(db_path=p).migrate()
    return p


def _qnr(db, answered=True):
    s = QuestionnaireStore(db_path=db)
    qid = s.create(from_agent="dev-demo", title="Sample", questions=[
        {"prompt": "Which colour?", "kind": "menu", "menu": {"options": [{"label": "Blue"}, {"label": "Green"}]}}])
    if answered:
        assert s.submit(qid, {"1": {"option_n": "2"}})["applied"] is True
    return s, qid


def _qack(db, qid):
    env = {**os.environ, "APPROVAL_DB_PATH": db}
    r = subprocess.run([sys.executable, os.path.join(HERE, "approval.py"), "qack", qid, "--from", "dev-demo"],
                       capture_output=True, text=True, env=env, timeout=60)
    return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])


def test_a_first_ack_is_unchanged(db):
    s, qid = _qnr(db)
    code, out = _qack(db, qid)
    assert code == 0 and out == {"acked": True, "id": qid}
    row = s.get(qid)
    assert row["status"] == "resumed" and row["resume_acked_at"]


def test_an_ack_after_a_live_delivery_reports_it_was_already_delivered(db):
    s, qid = _qnr(db)
    s.mark_resumed_fired(qid)
    assert s.ack(qid) is True                      # what a verified live delivery does
    before = s.get(qid)["resume_acked_at"]
    code, out = _qack(db, qid)
    assert code == 0 and out == {"acked": True, "already": True, "delivered": True, "id": qid}
    assert s.get(qid)["resume_acked_at"] == before  # nothing re-written


def test_a_questionnaire_never_answered_still_fails(db):
    _s, qid = _qnr(db, answered=False)
    code, out = _qack(db, qid)
    assert code == 1 and out == {"acked": False, "id": qid}


def test_an_unknown_questionnaire_still_fails(db):
    code, out = _qack(db, "qnr_00000000_0")
    assert code == 1 and out == {"acked": False, "id": "qnr_00000000_0"}
