"""A multi-part answer may SKIP parts: at least one part answered, skipped parts sent explicitly.

The card sends a part the person chose to leave unanswered as {part, skipped: true}. Everything
below runs the REAL code end to end on a stand-in pane: _clean_batch_answers (the route's
shape), _validate_batch_answer, durable_first_batch_submit on a real ApprovalStore (the durable
row must say "skipped"), and menu_batch_submit's replay, whose pane is a three-part Claude
AskUserQuestion that tracks each tab's answered mark the way the CLI draws it.

Why a skip is deliverable: Claude Code's review screen submits with unanswered questions. It
shows "You have not answered all questions" and still offers "1. Submit answers" (fixture
menus/askuserquestion_submit_confirm.pane.txt; the 2.1.295 bundle renders the same warning
conditionally and the Submit/Cancel control unconditionally).
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watch_gateway as G
import approval_resume as R
from approval_schema import ApprovalStore

SESSION = "dev-demo"
PARTS = [
    {"index": 0, "question": "Which colour?", "select": "single",
     "options": [{"n": "1", "label": "Blue"}, {"n": "2", "label": "Green"}]},
    {"index": 1, "question": "Which sizes?", "select": "multi",
     "options": [{"n": "1", "label": "Small"}, {"n": "2", "label": "Medium"}, {"n": "3", "label": "Large"}]},
    {"index": 2, "question": "Which finish?", "select": "single",
     "options": [{"n": "1", "label": "Matte"}, {"n": "2", "label": "Gloss"}]},
]
MENU = {"question": "Which colour?", "walk_complete": True, "part_count": 3, "parts": PARTS}


class Pane:
    """A three-part AskUserQuestion. A single-choice digit selects and moves on (the CLI's
    auto-advance); a multi-choice digit toggles a tick. The review screen reads each tab as
    answered or not; Enter on Submit closes the menu and records what was submitted."""

    def __init__(self, pre_ticked=None):
        self.pi = 0
        self.picks = {0: set(), 1: set(pre_ticked or ()), 2: set()}
        self.sent = []
        self.submitted = None

    def _tabs(self):
        return ["Which colour?", "Which sizes?", "Which finish?", "Submit"]

    def read(self):
        if self.submitted is not None:
            return None
        if self.pi >= 3:
            copts = [{"n": "1", "label": "Submit answers"}, {"n": "2", "label": "Cancel"}]
            return {"question": "Ready to submit your answers?", "part_count": 3, "part_index": 3,
                    "parts": [{"index": 0, "options": copts}], "options": copts, "has_submit": True,
                    "tabs": self._tabs(), "submit_tab_index": 3,
                    "tab_state": [{"label": t, "answered": bool(self.picks[i])}
                                  for i, t in enumerate(self._tabs()[:3])]}
        p = PARTS[self.pi]
        multi = p["select"] == "multi"
        opts = [{**o, **({"checked": o["n"] in self.picks[self.pi]} if multi else {})} for o in p["options"]]
        return {"question": p["question"], "part_count": 3, "part_index": self.pi,
                "parts": [{"index": self.pi, "question": p["question"], "options": opts}],
                "options": opts, "tabs": self._tabs(), "submit_tab_index": 3}

    def key(self, k):
        self.sent.append(k)
        if k == "Right":
            self.pi = min(3, self.pi + 1)
        elif k == "Left":
            self.pi = max(0, self.pi - 1)
        elif k == "Enter" and self.pi >= 3:
            self.submitted = {i: sorted(v) for i, v in self.picks.items()}
        elif k.isdigit() and self.pi < 3:
            if PARTS[self.pi]["select"] == "multi":
                self.picks[self.pi] ^= {k}
            else:
                self.picks[self.pi] = {k}
                self.pi += 1
        return True


@pytest.fixture
def store(tmp_path):
    s = ApprovalStore(db_path=str(tmp_path / "t.db"))
    s.migrate()
    s.create(from_agent="dev-demo", question="Which colour?", worker_kind="pane", kind="menu",
             menu=MENU, op_key=f"menu:{SESSION}:sample", options=["Blue", "Green"])
    return s


def _submit(store, raw, pane):
    """The route's cleaning, then the real orchestrator with the real replay on the pane."""
    answers, bad = G._clean_batch_answers(raw)
    assert bad is None, bad
    replay = lambda session, *, answers, armed, **kw: G.menu_batch_submit(
        session, answers=answers, armed=armed, read_fn=pane.read, key_fn=pane.key, settle_s=0, **kw)
    fallback = []
    ok, info = G.durable_first_batch_submit(SESSION, answers, store=store, armed=True,
                                            submit_fn=replay, resume_fn=lambda row, a: fallback.append(a))
    return ok, info, fallback


def _stored(store):
    row = store.latest_menu_row_for_session(SESSION)
    return row, json.loads(row["answer_text"]) if row and row.get("answer_text") else None


def test_all_three_answered_delivers_live(store):
    pane = Pane()
    raw = [{"part": 0, "ns": ["2"]}, {"part": 1, "ns": ["1", "3"]}, {"part": 2, "ns": ["1"]}]
    ok, info, fallback = _submit(store, raw, pane)
    assert ok and info["delivered"] is True, info
    assert pane.submitted == {0: ["2"], 1: ["1", "3"], 2: ["1"]}
    assert fallback == []
    assert _stored(store)[1] == raw


def test_skip_part_one_answer_two_and_three(store):
    pane = Pane()
    raw = [{"part": 0, "skipped": True}, {"part": 1, "ns": ["2"]}, {"part": 2, "ns": ["2"]}]
    ok, info, _ = _submit(store, raw, pane)
    assert ok and info["delivered"] is True, info
    assert pane.submitted == {0: [], 1: ["2"], 2: ["2"]}
    # nothing was pressed while on part 0: the first key is the Right that steps past it
    assert pane.sent[0] == "Right"
    row, stored = _stored(store)
    assert stored[0] == {"part": 0, "skipped": True}      # the record says skipped, not empty
    assert row["answer"] == "batch"


def test_answer_only_part_three(store):
    pane = Pane()
    raw = [{"part": 0, "skipped": True}, {"part": 1, "skipped": True}, {"part": 2, "ns": ["1"]}]
    ok, info, _ = _submit(store, raw, pane)
    assert ok and info["delivered"] is True, info
    assert pane.submitted == {0: [], 1: [], 2: ["1"]}
    assert [k for k in pane.sent if k.isdigit()] == ["1"]
    assert _stored(store)[1][:2] == [{"part": 0, "skipped": True}, {"part": 1, "skipped": True}]


def test_nothing_answered_is_refused_and_nothing_is_recorded_or_pressed(store):
    pane = Pane()
    raw = [{"part": i, "skipped": True} for i in range(3)]
    ok, info, _ = _submit(store, raw, pane)
    assert ok is False and info["reason"] == "batch_validation", info
    assert "nothing answered" in info["detail"]
    assert pane.sent == []
    row, stored = _stored(store)
    assert stored is None and row["status"] == "pending"


def test_a_part_simply_missing_is_still_refused(store):
    """Only an EXPLICIT skip is a skip; an absent part is a client bug, never a silent under-answer."""
    ok, info, _ = _submit(store, [{"part": 0, "ns": ["1"]}, {"part": 2, "ns": ["1"]}], Pane())
    assert ok is False and info["detail"] == "parts unanswered: [1]"


def test_an_empty_answer_is_not_a_skip(store):
    ok, info, _ = _submit(store, [{"part": 0, "ns": []}, {"part": 1, "skipped": True}, {"part": 2, "ns": ["1"]}], Pane())
    assert ok is False and "no option selected" in info["detail"]


def test_a_skip_carrying_an_answer_is_refused_at_the_route():
    assert G._clean_batch_answers([{"part": 0, "skipped": True, "ns": ["1"]}])[1] == "part 0: a skipped part cannot carry an answer"
    assert G._clean_batch_answers([{"part": 0, "skipped": True, "text": "x"}])[1] == "part 0: a skipped part cannot carry an answer"
    assert G._clean_batch_answers([{"part": 0, "skipped": True}]) == ([{"part": 0, "skipped": True}], None)


def test_a_skipped_part_already_ticked_on_screen_is_refused_not_unticked(store):
    """Someone ticked part 1 at the terminal; the person skipped it. Submitting would send an
    answer they chose to skip, and unticking would undo someone else's choice: refuse."""
    pane = Pane(pre_ticked={"2"})
    raw = [{"part": 0, "ns": ["1"]}, {"part": 1, "skipped": True}, {"part": 2, "ns": ["1"]}]
    ok, info, fallback = _submit(store, raw, pane)
    # durable already (the answer is recorded); the replay refused, so it went to the fallback
    assert ok and info["delivered"] is False and info["replay"]["reason"] == "skipped_part_answered_on_screen", info
    assert pane.submitted is None and pane.picks[1] == {"2"}
    assert fallback and fallback[0][1] == {"part": 1, "skipped": True}


def test_single_choice_pick_with_words_is_refused_multi_choice_keeps_both():
    row = {"kind": "menu", "menu": json.dumps({**MENU, "parts": [
        {**PARTS[0], "options": PARTS[0]["options"] + [{"n": "3", "label": "Type something.", "input_kind": "free_text"}]},
        {**PARTS[1], "options": PARTS[1]["options"] + [{"n": "4", "label": "Type something.", "input_kind": "free_text"}]},
        PARTS[2]]})}
    assert G._validate_batch_answer(row, [{"part": 0, "ns": ["1"], "text": "darker"}, {"part": 1, "skipped": True},
                                          {"part": 2, "skipped": True}]) == "part 0: pick an option or type your own words, not both"
    assert G._validate_batch_answer(row, [{"part": 0, "skipped": True}, {"part": 1, "ns": ["1"], "text": "XL too"},
                                          {"part": 2, "skipped": True}]) is None


def test_the_delivered_digest_says_skipped():
    row = {"id": "apr_s", "from_agent": "dev-demo", "menu": json.dumps(MENU)}
    body = R._menu_batch_digest_body(row, [{"part": 0, "skipped": True}, {"part": 1, "ns": ["1"]}, {"part": 2, "ns": ["2"]}])
    assert "[Which colour?: skipped]" in body and "[Which sizes?: Small]" in body and "[Which finish?: Gloss]" in body


def test_dry_run_plan_marks_the_skip():
    pane = Pane()
    ok, info = G.menu_batch_submit(SESSION, answers=[{"part": 0, "skipped": True}, {"part": 1, "ns": ["1"]}, {"part": 2, "ns": ["1"]}],
                                   armed=False, read_fn=pane.read, key_fn=pane.key, settle_s=0)
    assert ok and info["plan"][0] == {"part": 0, "skipped": True, "toggle": []}
    assert pane.sent == []


def test_agy_menus_refuse_a_skip_with_zero_keys():
    sent = []
    ok, info = G.menu_batch_submit(SESSION, answers=[{"part": 0, "skipped": True}, {"part": 1, "ns": ["1"]}], armed=True,
                                   read_fn=lambda: {"menu_family": "agy", "question": "q"},
                                   key_fn=lambda k: sent.append(k) or True, settle_s=0)
    assert ok is False and info["reason"] == "skip_not_supported" and sent == []
