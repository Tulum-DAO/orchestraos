"""The cards on the operator's screen (contract/notes/arturo-screen-context.md): the bounded record, the block the
brain reads for one turn, and the answer_screen_card tool's rules. Pure: no proxy, no state, no network."""
import json
import pathlib
import re

import pytest

from services.arturo import screen_cards as sc

HERE = pathlib.Path(__file__).resolve().parent
CASES = json.loads((HERE / "fixtures" / "screen_cards_cases.json").read_text())
CARD_ID = re.compile(r"\b(apr|qnr)_[0-9a-f]{8}_\d+")


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_record_matches_the_api_rules(case):
    assert sc.pick_cards(case["in"]) == case["out"]


def test_a_value_that_is_no_cards_record_is_refused():
    for raw in (None, "cards", [], {"cards": "x"}, {"focus": "c1"}):
        assert sc.pick_cards(raw) is None


def _screen(focus="c1"):
    return sc.pick_cards({"focus": focus, "cards": [
        {"ref": "c1", "kind": "multipart", "from": "pm-demo", "state": "waiting", "surface": "popover",
         "question": "Which size?", "part": {"n": 2, "of": 3, "question": "Which colour?", "select": "single",
                                            "options": ["A. Blue", "B. Green"], "picked": ["A. Blue"]}},
        {"ref": "c2", "line": "Approval from dev-demo, waiting: Deploy the drawer?"},
    ]})


def test_the_focused_card_leads_with_its_current_part_and_options():
    block = sc.cards_block(_screen())
    lines = block.splitlines()
    assert lines[1].startswith("- c1 [IN FOCUS] Multi-part question from pm-demo, waiting")
    assert 'Part 2 of 3, the CURRENT question: "Which colour?" (pick one)' in block
    assert "Its options: A. Blue; B. Green" in block
    assert "Picked so far on this part: A. Blue" in block
    assert "- c2 Approval from dev-demo, waiting: Deploy the drawer?" in block
    assert "never say a ref or any id" in block
    assert not CARD_ID.search(block)


def test_several_cards_and_none_in_focus_asks_naming_them_by_their_questions():
    block = sc.cards_block(_screen(focus=None))
    assert "[IN FOCUS]" not in block
    assert "reply with only the question of which one" in block
    assert "do not describe the cards, guess or offer help with each" in block
    assert 'like this: The "Which size?" card or the "Deploy the drawer?" card? Then wait' in block


def test_one_card_and_no_focus_is_still_this():
    one = sc.pick_cards({"cards": [{"ref": "c1", "kind": "decision", "question": "Which size?", "options": ["A. Circle"]}]})
    assert '"this" means it' in sc.cards_block(one)


def test_no_cards_no_block():
    assert sc.cards_block(None) == ""
    assert sc.cards_block({"v": 1, "focus": None, "cards": []}) == ""


def _turn(refs=("c1", "c2"), principal="fleet"):
    return {"principal": principal, "screen_refs": list(refs)}


def test_an_answer_is_kept_on_the_turn_for_the_card_on_screen_to_send():
    turn = _turn()
    out = sc.answer_for(turn, True, "c1", ["B", "B", " "], "  bigger  please ")
    assert out.startswith("HANDED TO THE CARD")
    assert turn["answer_card"] == {"card": "c1", "picks": ["B"], "words": "bigger please"}


@pytest.mark.parametrize("turn,fleet,card,picks,words,why", [
    (None, True, "c1", ["A"], None, "only from the dashboard"),
    ({"principal": "device:dev_x", "screen_refs": ["c1"]}, True, "c1", ["A"], None, "only from the dashboard"),
    ({"principal": "fleet", "screen_refs": ["c1"]}, False, "c1", ["A"], None, "only from the dashboard"),
    ({"principal": "fleet", "screen_refs": ["c1"]}, True, "c3", ["A"], None, "no such card"),
    ({"principal": "fleet", "screen_refs": ["c1"]}, True, "c1", [], "", "say which option"),
    ({"principal": "fleet", "screen_refs": ["c1"], "answer_card": {"card": "c1"}}, True, "c1", ["A"], None, "one card per turn"),
])
def test_an_answer_is_refused(turn, fleet, card, picks, words, why):
    before = dict(turn or {})
    out = sc.answer_for(turn, fleet, card, picks, words)
    assert out.startswith("NOT SENT") and why in out
    assert (turn or {}) == before


def test_refs_are_the_cards_a_tool_may_name():
    assert sc.refs(_screen()) == ["c1", "c2"]
    assert sc.refs(None) == []


def test_asking_names_cards_by_their_questions_without_part_notes():
    screen = sc.pick_cards({"cards": [
        {"ref": "c1", "line": "Decision from pm-demo, waiting: Which size?"},
        {"ref": "c2", "line": "Multi-part question from pm-demo, waiting: Which layout? (part 2 of 2: Which colour?)"},
    ]})
    assert 'The "Which size?" card or the "Which layout?" card?' in sc.cards_block(screen)
