"""Arturo's choice cards are kept with his reply in the thread: shown again after a reload, and closed unanswered by
id (dismissed) without leaving the thread."""
from services.arturo.thread_store import ThreadStore

CARD = {"options": ["Alpha", "Beta"], "multi": False, "purpose": "other", "card_id": "c0ffee01"}


def _store(tmp_path):
    return ThreadStore(tmp_path / "threads.db")


def test_a_reply_keeps_the_card_it_showed(tmp_path):
    st = _store(tmp_path)
    assert st.record_turn("web_sample", "Start", "Which one?",
                          assistant_meta={"kind": "card", "card_id": "c0ffee01", "choices": CARD})
    turns = st.get_thread("web_sample")["turns"]
    assert turns[0].get("meta") in (None, {})                      # the user's message carries nothing extra
    assert turns[1]["meta"] == {"kind": "card", "card_id": "c0ffee01", "choices": CARD}


def test_a_reply_without_a_card_is_stored_as_before(tmp_path):
    st = _store(tmp_path)
    st.record_turn("web_sample", "Hi", "Hello")
    assert not st.get_thread("web_sample")["turns"][1].get("meta")


def test_dismissing_marks_that_card_only(tmp_path):
    st = _store(tmp_path)
    st.record_turn("web_sample", "Start", "Which one?", assistant_meta={"kind": "card", "card_id": "c0ffee01", "choices": CARD})
    st.record_turn("web_sample", "Beta", "And this?", assistant_meta={"kind": "card", "card_id": "c0ffee02", "choices": CARD})
    assert st.dismiss_card("web_sample", "c0ffee02") is True
    turns = st.get_thread("web_sample")["turns"]
    assert turns[1]["meta"].get("dismissed") is None
    assert turns[3]["meta"]["dismissed"] is True
    assert len(turns) == 4                                          # nothing left the thread


def test_an_unknown_card_or_thread_is_not_found(tmp_path):
    st = _store(tmp_path)
    st.record_turn("web_sample", "Start", "Which one?", assistant_meta={"kind": "card", "card_id": "c0ffee01", "choices": CARD})
    assert st.dismiss_card("web_sample", "deadbeef") is False
    assert st.dismiss_card("web_other", "c0ffee01") is False
    assert st.dismiss_card("web_sample", "") is False
