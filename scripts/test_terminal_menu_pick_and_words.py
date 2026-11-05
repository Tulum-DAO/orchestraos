"""A menu bridged from an agent's terminal takes a pick OR typed words, never both.

The pane can deliver only one: a digit fires the option, words go through the write-in row. A pick sent with words
used to be accepted with the words stored as a note that never reached the agent. Now it is refused with what to do.
A decision card an agent posts itself (not a terminal menu) still takes both. Runs the REAL apply_answer on a real
ApprovalStore in a temp database, with no resume fired.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watch_gateway as G  # noqa: E402
from approval_schema import ApprovalStore  # noqa: E402

MENU = {"question": "Which sample should run?", "options": [
    {"n": "1", "label": "Alpha", "input_kind": "direct"},
    {"n": "2", "label": "Beta", "input_kind": "direct"},
    {"n": "3", "label": "Type something.", "input_kind": "free_text"}]}
REFUSED = "pick an option or type your own answer, not both"


@pytest.fixture
def store(tmp_path):
    s = ApprovalStore(db_path=str(tmp_path / "t.db"))
    s.migrate()
    return s


def _row(store, op_key, origin=None):
    return store.create(from_agent="dev-demo", question=MENU["question"], worker_kind="pane", kind="menu",
                        menu=MENU, options=[o["label"] for o in MENU["options"]], op_key=op_key, origin=origin)


def _answer(store, rid, **kw):
    return G.apply_answer(store, rid, "option", fire=False, surface="web", **kw)


def test_a_terminal_menu_refuses_a_pick_with_words_and_stays_pending(store):
    rid = _row(store, "menu:dev-demo:0123456789abcdef", origin="menu_bridge")
    r = _answer(store, rid, option_n="1", answer_text="but after lunch")
    assert r["ok"] is False and r["status"] == 400 and r["error"] == REFUSED
    assert store.get(rid)["status"] == "pending"
    # then a pick alone works
    r = _answer(store, rid, option_n="1")
    assert r["ok"] is True and r["applied"] is True
    row = store.get(rid)
    assert row["option_n"] == "1" and not row.get("answer_text")


def test_a_terminal_menu_is_known_by_its_key_even_without_the_origin_column(store):
    rid = _row(store, "menu:dev-demo:fedcba9876543210")
    assert _answer(store, rid, option_n="2", answer_text="x")["error"] == REFUSED


def test_words_alone_on_a_terminal_menu_still_go_through_its_write_in(store):
    rid = _row(store, "menu:dev-demo:00000000000000aa", origin="menu_bridge")
    r = _answer(store, rid, answer_text="run it after lunch")
    assert r["ok"] is True and r["applied"] is True
    row = store.get(rid)
    assert row["option_n"] == "3" and row["answer_text"] == "run it after lunch"


def test_the_write_in_option_with_its_words_is_one_answer_not_both(store):
    rid = _row(store, "menu:dev-demo:00000000000000bb", origin="menu_bridge")
    assert _answer(store, rid, option_n="3", answer_text="run it after lunch")["ok"] is True


def test_a_posted_decision_card_takes_a_pick_and_words_together(store):
    rid = _row(store, "sample-posted-1")
    r = _answer(store, rid, option_n="2", answer_text="but after lunch")
    assert r["ok"] is True and r["applied"] is True
    row = store.get(rid)
    assert row["option_n"] == "2" and row["answer_text"] == "but after lunch"
