"""Arturo's choice cards answered on the server: the answer rides with the turn that answers the card and is kept on
the card's row in the same write. Hermetic: a ThreadStore in a temp dir, no proxy, no network."""
import pytest

from services.arturo import card_answers as ca
from services.arturo.thread_store import ThreadStore

CARD = "0a1b2c3d4e5f6a7b"  # pragma: allowlist secret
CHOICES = {"options": ["Circle", "Square", "Triangle"], "multi": False, "purpose": "other", "card_id": CARD}


def _store(tmp_path, choices=CHOICES):
    s = ThreadStore(tmp_path / "threads.db")
    s.record_turn("web_sample", "Pick a shape", "Which shape?", ts=1.0,
                  assistant_meta={"kind": "card", "card_id": CARD, "choices": choices})
    return s


def test_an_answer_is_rebuilt_from_known_fields():
    got, refusal = ca.pick({"card_id": CARD, "picks": [" Circle ", "Circle", ""], "words": " a big one ", "x": 1})
    assert refusal is None
    assert got == {"card_id": CARD, "picks": ["Circle"], "words": "a big one"}
    assert ca.pick(None) == (None, None)


@pytest.mark.parametrize("raw", ["x", {}, {"card_id": "NOT-HEX"}, {"card_id": CARD}, {"card_id": CARD, "picks": "Circle"},
                                 {"card_id": CARD, "picks": ["a"] * 9}, {"card_id": CARD, "words": "x" * 1001},
                                 {"card_id": CARD, "picks": [], "words": "  "}])
def test_a_malformed_answer_is_refused(raw):
    got, refusal = ca.pick(raw)
    assert got is None and refusal["error"] == "bad_card_answer"


def test_the_card_takes_one_answer_and_keeps_it_with_the_turn(tmp_path):
    s = _store(tmp_path)
    answer, _ = ca.pick({"card_id": CARD, "picks": ["Square"]})
    assert ca.check(s.card("web_sample", CARD), answer) is None
    assert s.record_turn("web_sample", "Square", "Square it is.", ts=2.0,
                         card_answer={"card_id": CARD, "answer": ca.stored(answer, 2.0)})
    card = s.card("web_sample", CARD)
    assert card["answer"] == {"picks": ["Square"], "at": 2.0}
    # A second, different send answering it is refused, with the stored answer.
    again, _ = ca.pick({"card_id": CARD, "picks": ["Circle"]})
    refusal = ca.check(s.card("web_sample", CARD), again)
    assert refusal["error"] == "card_answered" and refusal["answer"]["picks"] == ["Square"]
    # The store keeps the first answer even if a second write slipped past the checks.
    s.record_turn("web_sample", "Circle", "ok", ts=3.0, card_answer={"card_id": CARD, "answer": ca.stored(again, 3.0)})
    assert s.card("web_sample", CARD)["answer"]["picks"] == ["Square"]
    assert len(s.get_thread("web_sample")["turns"]) == 6


def test_an_answered_card_cannot_be_dismissed_and_a_dismissed_one_cannot_be_answered(tmp_path):
    s = _store(tmp_path)
    answer, _ = ca.pick({"card_id": CARD, "words": "none of those"})
    s.record_turn("web_sample", "none of those", "Fair.", ts=2.0, card_answer={"card_id": CARD, "answer": ca.stored(answer, 2.0)})
    assert s.card("web_sample", CARD)["answer"] == {"picks": [], "words": "none of those", "at": 2.0}
    assert s.dismiss_card("web_sample", CARD) is False
    t = _store(tmp_path / "other")
    assert t.dismiss_card("web_sample", CARD) is True
    assert ca.check(t.card("web_sample", CARD), answer)["error"] == "card_dismissed"


def test_unknown_cards_and_options_it_does_not_have_are_refused(tmp_path):
    s = _store(tmp_path)
    answer, _ = ca.pick({"card_id": "ffffffffffffffff", "picks": ["Circle"]})
    assert ca.check(s.card("web_sample", "ffffffffffffffff"), answer)["error"] == "card_unknown"
    hexagon, _ = ca.pick({"card_id": CARD, "picks": ["Hexagon"]})
    assert ca.check(s.card("web_sample", CARD), hexagon)["field"] == "card_answer.picks"
    two, _ = ca.pick({"card_id": CARD, "picks": ["Circle", "Square"]})
    assert ca.check(s.card("web_sample", CARD), two)["field"] == "card_answer.picks"
    multi = _store(tmp_path / "multi", {**CHOICES, "multi": True})
    assert ca.check(multi.card("web_sample", CARD), two) is None


def test_the_card_answer_comes_back_with_the_thread(tmp_path):
    s = _store(tmp_path)
    answer, _ = ca.pick({"card_id": CARD, "picks": ["Triangle"], "words": "pointy"})
    s.record_turn("web_sample", "Triangle — pointy", "Noted.", ts=2.0, card_answer={"card_id": CARD, "answer": ca.stored(answer, 2.0)})
    thread = s.get_thread("web_sample")
    metas = [t.get("meta") for t in thread["turns"] if t.get("meta")]
    assert metas[0]["answer"] == {"picks": ["Triangle"], "words": "pointy", "at": 2.0}
