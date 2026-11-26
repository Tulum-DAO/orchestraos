"""Reading Shaw's answers back (design §4): act only on a submitted questionnaire's FINAL answers, apply
once, then qack so the resume watchdog stops retrying. Proven end to end against the scratch DB."""
import os
import sqlite3
import sys

from postcall import effects, ledger, pipeline
from postcall.effects import Effect

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
APPROVAL_CLI = os.path.join(REPO, "scripts", "approval.py")

QUESTIONS = [{"prompt": "How should I handle the items below?", "kind": "menu", "required": True,
              "menu": {"options": [{"label": "Accept"}, {"label": "Per item"}, {"label": "Drop all"}]}},
             {"prompt": "Do a1 · Dana Reyes · Due: unknown", "kind": "menu", "required": False,
              "menu": {"options": [{"label": "Take it myself (suggested)"}, {"label": "Drop"}]}}]
ITEM = {"owner": "shaw", "text": "Do a1", "counterpart": "Dana Reyes", "due_ts": None, "due_source": "none stated",
        "quote": "q", "citation": "pocket:r1@00:10", "call_title": "T"}
FROZEN = [{"q_n": 2, "key": "src_a1", "options": [{"action": "take"}, {"action": "drop"}], "suggested_n": 1,
           "flagged": False, "item": ITEM}]


class Answering(effects.Recorder):
    """Test world where Shaw's answers can be set per filed card."""

    def __init__(self):
        super().__init__()
        self.answers, self.acked = {}, []

    def card_answers(self, external_id):
        return self.answers.get(external_id, ("pending", {}))

    def send(self, effect):
        if effect.kind == "qack":
            self.acked.append(effect.payload["questionnaire"])
        return super().send(effect)


def _filed(led, world):
    card = Effect("card:pocket:r1:r0:p1", "card", {"title": "T: 1 items", "summary": "Decide by: x",
                                                   "questions": QUESTIONS, "frozen": FROZEN})
    led.plan_card(card, call_id="pocket:r1", frozen=FROZEN)
    led.run_pending(world)
    return {e["effect_id"]: e for e in led.effects()}[card.effect_id]["external_id"]


def test_pending_card_is_left_alone(tmp_path):
    led, world = ledger.Ledger(str(tmp_path / "l.db")), Answering()
    _filed(led, world)
    pipeline.apply_submitted(led, world)
    assert led.decision("src_a1") is None and world.acked == []


def test_submitted_card_is_applied_once_and_acked_once(tmp_path):
    led, world = ledger.Ledger(str(tmp_path / "l.db")), Answering()
    ext = _filed(led, world)
    world.answers[ext] = ("submitted", {1: {"option_n": 1, "final": True}})
    pipeline.apply_submitted(led, world)
    pipeline.apply_submitted(led, world)
    assert led.decision("src_a1") == "take"
    assert [e.kind for e in world.sent] == ["card", "task", "qack"]
    assert world.acked == [ext]


def test_live_end_to_end_shaw_submits_in_the_scratch_db(tmp_path, request, monkeypatch):
    request.node.postcall_allow_argv = [[sys.executable, APPROVAL_CLI]]
    monkeypatch.setenv("POSTCALL_APPROVAL_CLI", APPROVAL_CLI)
    led, live = ledger.Ledger(str(tmp_path / "l.db")), effects.adapter_for("live")
    qid = _filed(led, live)
    assert qid.startswith("qnr_")
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    from questionnaire_schema import QuestionnaireStore
    qs = QuestionnaireStore(db_path=os.environ["APPROVAL_DB_PATH"])
    assert live.card_answers(qid)[0] == "pending"
    qs.save_draft(qid, {"1": {"option_n": "2"}, "2": {"option_n": "1"}})
    pipeline.apply_submitted(led, live)          # drafts are never acted on
    assert led.decision("src_a1") is None
    assert qs.submit(qid)["applied"] is True
    pipeline.apply_submitted(led, live)
    assert led.decision("src_a1") == "take"
    with sqlite3.connect(os.environ["APPROVAL_DB_PATH"]) as c:
        status = c.execute("SELECT status FROM questionnaires WHERE id=?", (qid,)).fetchone()[0]
        task = c.execute("SELECT kind, feature, notified_at FROM approval_requests WHERE from_agent='postcall-dev' "
                         "AND op_key=?", (led.commitment_of("src_a1"),)).fetchone()
    assert status == "resumed"
    assert task == ("human_task", "commitment", None)


def test_live_answer_mapping_marks_only_final_rows_final(monkeypatch):
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    from questionnaire_schema import QuestionnaireStore
    db = os.environ["APPROVAL_DB_PATH"]
    QuestionnaireStore(db_path=db).migrate()
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO questionnaires(id, from_agent, title, status, question_count, created_at) "
                  "VALUES ('qnr_t', 'postcall-dev', 't', 'submitted', 3, '2026-11-25T00:00:00Z')")
        c.executemany("INSERT INTO questionnaire_answers(qnr_id, n, option_n, state, updated_at) VALUES (?,?,?,?,?)",
                      [("qnr_t", 1, "1", "final", "x"), ("qnr_t", 2, "2", "draft", "x"),
                       ("qnr_t", 3, None, "skipped", "x")])
    status, answers = effects.adapter_for("live").card_answers("qnr_t")
    assert status == "submitted"
    assert answers == {1: {"option_n": 1, "final": True}, 2: {"option_n": 2, "final": False},
                       3: {"option_n": None, "final": False}}
