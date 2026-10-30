"""A text turn that answers one of Arturo's choice cards (card_answer): the answer is kept on the card with the turn,
and a second, different send answering it is refused before anything runs. Drives the real text_turn with the brains
stubbed (the shared fixture from test_text_turn_brain.py: threads in a temp dir)."""
from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture

CARD = "0a1b2c3d4e5f6a7b"
CHOICES = {"options": ["Circle", "Square"], "multi": False, "purpose": "other", "card_id": CARD}


def _seed(P, cid):
    P._THREADS.record_turn(cid, "Pick a shape", "Which shape?", ts=1.0,
                           assistant_meta={"kind": "card", "card_id": CARD, "choices": CHOICES})


def _rows(P, cid):
    return len(P._THREADS.get_thread(cid)["turns"])


def test_the_answer_reaches_the_brain_as_words_and_is_kept_on_the_card(P):
    _seed(P, "web_ca1")
    code, body = P.text_turn("Square", "web_ca1", principal="fleet", card_answer={"card_id": CARD, "picks": ["Square"]})
    assert code == 200 and body["reply_text"] == "from the default brain"
    assert any("Square" in str(m.get("content")) for m in P._test.calls["global_messages"])
    assert P._THREADS.card("web_ca1", CARD)["answer"]["picks"] == ["Square"]
    assert _rows(P, "web_ca1") == 4


def test_a_second_answer_is_refused_and_nothing_runs(P):
    _seed(P, "web_ca2")
    P.text_turn("Square", "web_ca2", principal="fleet", card_answer={"card_id": CARD, "picks": ["Square"]})
    before, calls = _rows(P, "web_ca2"), P._test.calls["global"]
    code, body = P.text_turn("Circle", "web_ca2", principal="fleet", card_answer={"card_id": CARD, "picks": ["Circle"]})
    assert code == 409 and body["error"] == "card_answered" and body["answer"]["picks"] == ["Square"]
    assert _rows(P, "web_ca2") == before and P._test.calls["global"] == calls


def test_a_dismissed_or_unknown_card_or_a_bad_answer_runs_nothing(P):
    _seed(P, "web_ca3")
    P._THREADS.dismiss_card("web_ca3", CARD)
    code, body = P.text_turn("Square", "web_ca3", principal="fleet", card_answer={"card_id": CARD, "picks": ["Square"]})
    assert code == 409 and body["error"] == "card_dismissed"
    code, body = P.text_turn("Square", "web_ca3", principal="fleet", card_answer={"card_id": "ffffffffffffffff", "picks": ["Square"]})
    assert code == 409 and body["error"] == "card_unknown"
    code, body = P.text_turn("Square", "web_ca3", principal="fleet", card_answer={"card_id": "nope"})
    assert code == 400 and body["error"] == "bad_card_answer"
    assert P._test.calls["global"] == 0 and _rows(P, "web_ca3") == 2


def test_a_turn_with_no_card_answer_is_unchanged(P):
    _seed(P, "web_ca4")
    code, _ = P.text_turn("Something else", "web_ca4", principal="fleet")
    assert code == 200
    assert "answer" not in P._THREADS.card("web_ca4", CARD)
