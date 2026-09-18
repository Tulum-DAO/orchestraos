"""RED-first for G20: Arturo threads are durable, server-side objects.

The pill and the home each kept ONE conversation in localStorage, so "New thread" lost the
previous one. These tests pin the server side of the fix: a thread list that survives a
service restart (a fresh store object over the same file), titles derived from the first
user message, and per-thread turns readable back in order.
"""
import time

import pytest

from services.arturo.thread_store import ThreadStore


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "threads.db"


def test_turns_recorded_are_listed_as_a_thread(db):
    s = ThreadStore(db)
    s.record_turn("c1", "How many agents are up?", "Nine seats are up.")
    threads = s.list_threads()
    assert [t["id"] for t in threads] == ["c1"]
    t = threads[0]
    assert t["title"] == "How many agents are up?"
    assert t["turns"] == 2                      # user + assistant
    assert t["snippet"] == "Nine seats are up."
    assert t["updated"] >= t["created"] > 0


def test_a_fresh_store_over_the_same_file_still_has_the_thread(db):
    """The acceptance case: the thread survives a SERVICE RESTART, so it cannot be
    in-memory state. A second ThreadStore is the restart."""
    ThreadStore(db).record_turn("c1", "first question", "first answer")
    revived = ThreadStore(db)
    assert [t["id"] for t in revived.list_threads()] == ["c1"]
    assert revived.get_thread("c1")["turns"][0]["content"] == "first question"


def test_threads_list_newest_first_and_paging_is_bounded(db):
    s = ThreadStore(db)
    for i in range(5):
        s.record_turn(f"c{i}", f"question {i}", f"answer {i}")
        time.sleep(0.002)
    assert [t["id"] for t in s.list_threads()] == ["c4", "c3", "c2", "c1", "c0"]
    assert [t["id"] for t in s.list_threads(limit=2)] == ["c4", "c3"]
    page2 = s.list_threads(limit=2, offset=2)
    assert [t["id"] for t in page2] == ["c2", "c1"]


def test_a_second_turn_updates_the_thread_without_changing_its_title(db):
    s = ThreadStore(db)
    s.record_turn("c1", "the first thing I asked", "answer one")
    created = s.list_threads()[0]["created"]
    time.sleep(0.002)
    s.record_turn("c1", "a later follow-up", "answer two")
    t = s.list_threads()[0]
    assert t["title"] == "the first thing I asked"   # title is the FIRST user message
    assert t["snippet"] == "answer two"              # snippet is the LATEST reply
    assert t["turns"] == 4
    assert t["created"] == created and t["updated"] > created


def test_get_thread_returns_its_turns_in_order_and_none_for_an_unknown_id(db):
    s = ThreadStore(db)
    s.record_turn("c1", "q1", "a1")
    s.record_turn("c1", "q2", "a2")
    turns = s.get_thread("c1")["turns"]
    assert [(t["role"], t["content"]) for t in turns] == [
        ("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2")]
    assert s.get_thread("nope") is None


def test_titles_are_derived_not_asked_for_and_are_bounded(db):
    s = ThreadStore(db)
    s.record_turn("c1", "  x" * 200, "ok")
    title = s.list_threads()[0]["title"]
    assert len(title) <= 80 and title.strip() == title and title


def test_history_for_the_brain_comes_back_as_role_content_pairs(db):
    """Continuing an OLD thread after a restart must re-thread context, so the store is
    also what rehydrates the in-memory history."""
    s = ThreadStore(db)
    s.record_turn("c1", "q1", "a1")
    assert ThreadStore(db).history("c1") == [
        {"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
    assert ThreadStore(db).history("unknown") == []


def test_history_is_bounded_to_the_last_turns(db):
    s = ThreadStore(db)
    for i in range(20):
        s.record_turn("c1", f"q{i}", f"a{i}")
    h = s.history("c1", max_turns=6)
    assert len(h) == 6 and h[0]["content"] == "q17" and h[-1]["content"] == "a19"


def test_a_store_whose_file_cannot_be_written_degrades_instead_of_killing_a_turn(tmp_path):
    """A turn must never fail because the archive is unwritable: three states, and the
    unusable one is silent for the operator's turn but visible to a caller that asks."""
    bad = tmp_path / "not-a-dir" / "threads.db"
    (tmp_path / "not-a-dir").write_text("i am a file, not a directory")
    s = ThreadStore(bad)
    assert s.usable is False
    s.record_turn("c1", "q", "a")      # must not raise
    assert s.list_threads() == [] and s.get_thread("c1") is None and s.history("c1") == []
