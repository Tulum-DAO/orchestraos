"""A chat can be renamed by the operator, and an onboarding chat reads "Getting started".

A title used to be only the first user message (derive_title), written while empty. A name the operator chose must
never be overwritten by that auto-title, must not be cleaned as if it were a stored message, and an empty name puts
the auto-title back.
"""
import sqlite3

import pytest

from services.arturo.thread_store import ONBOARDING_TITLE, TITLE_MAX, ThreadStore

OPENER = "(first run: the operator just opened the page)"


@pytest.fixture()
def s(tmp_path):
    return ThreadStore(tmp_path / "threads.db")


def test_rename_sets_the_title_every_read_shows(s):
    s.record_turn("c1", "How many agents are up?", "Nine.")
    assert s.rename("c1", "Agent count") == {"id": "c1", "title": "Agent count", "title_custom": True}
    assert s.list_threads()[0]["title"] == "Agent count"
    assert s.list_threads()[0]["title_custom"] is True
    assert s.get_thread("c1")["title"] == "Agent count"
    assert s.get_thread_page("c1")["title"] == "Agent count"


def test_an_auto_title_is_not_custom(s):
    s.record_turn("c1", "How many agents are up?", "Nine.")
    assert s.list_threads()[0]["title_custom"] is False


def test_a_later_turn_never_overwrites_a_chosen_title(s):
    s.record_turn("c1", OPENER, "Hi, what should I call you?")      # no auto-title yet
    s.rename("c1", "Setup")
    s.record_turn("c1", "call me Sam", "Hi Sam.")
    s.append_message("c1", "user", "another line said on a call", mode="voice")
    assert s.get_thread("c1")["title"] == "Setup"


def test_rename_trims_and_keeps_one_line(s):
    s.record_turn("c1", "q", "a")
    assert s.rename("c1", "  Two\n  lines\there  ")["title"] == "Two lines here"


def test_rename_refuses_a_title_over_the_limit(s):
    s.record_turn("c1", "q", "a")
    with pytest.raises(ValueError):
        s.rename("c1", "x" * (TITLE_MAX + 1))
    assert s.rename("c1", "x" * TITLE_MAX)["title"] == "x" * TITLE_MAX


def test_an_empty_name_puts_the_auto_title_back(s):
    s.record_turn("c1", "How many agents are up?", "Nine.")
    s.rename("c1", "Agent count")
    assert s.rename("c1", "   ") == {"id": "c1", "title": "How many agents are up?", "title_custom": False}
    assert s.list_threads()[0]["title"] == "How many agents are up?"


def test_rename_of_an_unknown_thread_is_none(s):
    assert s.rename("nope", "x") is None


def test_a_chosen_title_is_shown_as_typed(s):
    """_clean_title strips a stored context line and skips the page's opener: a chosen name is neither."""
    s.record_turn("c1", "q", "a")
    s.rename("c1", "[Context: route=/x] my notes")
    assert s.list_threads()[0]["title"] == "[Context: route=/x] my notes"
    s.rename("c1", "(first run: how it went)")
    assert s.get_thread("c1")["title"] == "(first run: how it went)"


def test_an_onboarding_chat_reads_getting_started(s):
    s.record_turn("c1", OPENER, "Hi, what should I call you?")
    assert s.list_threads()[0]["title"] == ONBOARDING_TITLE == "Getting started"
    s.record_turn("c1", "call me Sam", "Hi Sam.")
    assert s.get_thread("c1")["title"] == "Getting started"     # not the operator's first answer
    s.rename("c1", "Day one")
    assert s.get_thread("c1")["title"] == "Day one"
    assert s.rename("c1", "")["title"] == "Getting started"


def test_an_existing_onboarding_row_reads_getting_started(tmp_path):
    """A row written before this change (title '' or the first real answer) reads the same way."""
    db = tmp_path / "threads.db"
    s = ThreadStore(db)
    s.record_turn("c1", OPENER, "Hi")
    s.record_turn("c1", "call me Sam", "Hi Sam.")
    with sqlite3.connect(db) as c:
        c.execute("UPDATE threads SET title = 'call me Sam'")
    assert ThreadStore(db).list_threads()[0]["title"] == "Getting started"


def test_search_finds_a_chat_by_its_chosen_title(s):
    s.record_turn("c1", "unrelated words", "reply")
    s.rename("c1", "Budget planning")
    assert [t["id"] for t in s.search("budget")] == ["c1"]
    assert s.search("budget")[0]["title"] == "Budget planning"


def test_an_old_database_gains_the_column(tmp_path):
    db = tmp_path / "threads.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', created REAL NOT NULL,"
                  " updated REAL NOT NULL, turns INTEGER NOT NULL DEFAULT 0, snippet TEXT NOT NULL DEFAULT '')")
        c.execute("INSERT INTO threads VALUES ('old', 'old title', 1, 1, 0, '')")
    s = ThreadStore(db)
    assert s.list_threads()[0]["title"] == "old title"
    assert s.rename("old", "new title")["title"] == "new title"


# --- delete ----------------------------------------------------------------------------------------------------

def _counts(db, cid):
    with sqlite3.connect(db) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t} WHERE {'id' if t == 'threads' else 'thread_id'} = ?",
                             (cid,)).fetchone()[0]
                for t in ("threads", "turns", "turn_marks", "summaries", "untrusted")}


def test_delete_removes_the_chat_from_every_table(tmp_path):
    db = tmp_path / "threads.db"
    s = ThreadStore(db)
    s.mark_started("c1", "t1", "owner", "boot")
    s.record_turn("c1", "the zebra password is 1234", "noted", turn_id="t1")
    s.set_summary("c1", 2, "talked about a zebra password")
    s.mark_untrusted("c1")
    s.record_turn("c2", "keep me", "kept")
    assert all(_counts(db, "c1").values())
    gone = s.delete_thread("c1")
    assert gone["id"] == "c1"
    assert ("user", "the zebra password is 1234") in [(m["role"], m["content"]) for m in gone["messages"]]
    assert _counts(db, "c1") == dict.fromkeys(("threads", "turns", "turn_marks", "summaries", "untrusted"), 0)
    assert [t["id"] for t in s.list_threads()] == ["c2"]
    assert s.get_thread("c1") is None


def test_deleted_words_do_not_stay_in_the_database_file(tmp_path):
    db = tmp_path / "threads.db"
    s = ThreadStore(db)
    s.record_turn("c1", "zebrasecretphrase " * 20, "okay")
    s.record_turn("c2", "keep me", "kept")
    s.delete_thread("c1")
    assert b"zebrasecretphrase" not in db.read_bytes()


def test_delete_of_an_unknown_chat_is_none(s):
    assert s.delete_thread("nope") is None
