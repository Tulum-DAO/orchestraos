"""RED-first: threads remember which brain answered (DEC-1790669162399904 spec v4 §1.4).

Per-turn brain on each assistant row, `last_brain` on the thread, and an idempotent migration,
because `CREATE TABLE IF NOT EXISTS` never adds columns to a threads.db that already exists.
"""
import sqlite3

import pytest

from services.arturo.thread_store import ThreadStore

OLD_SCHEMA = """
CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
                      updated REAL NOT NULL, turns INTEGER NOT NULL DEFAULT 0, snippet TEXT NOT NULL DEFAULT '');
CREATE TABLE turns (thread_id TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL,
                    content TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY (thread_id, seq));
INSERT INTO threads VALUES ('old', 'An old thread', 1.0, 2.0, 2, 'old reply');
INSERT INTO turns VALUES ('old', 0, 'user', 'hi', 1.0), ('old', 1, 'assistant', 'old reply', 2.0);
"""


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "threads.db"


def _old_db(path):
    with sqlite3.connect(str(path)) as c:
        c.executescript(OLD_SCHEMA)


def _cols(path, table):
    with sqlite3.connect(str(path)) as c:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def test_opening_a_pre_migration_db_adds_the_brain_columns(db):
    _old_db(db)
    s = ThreadStore(db)
    assert s.usable
    assert {"last_brain_provider", "last_brain_model"} <= _cols(db, "threads")
    assert {"brain_provider", "brain_model"} <= _cols(db, "turns")


def test_old_rows_survive_migration_and_read_as_default_brain(db):
    _old_db(db)
    s = ThreadStore(db)
    t = s.list_threads()[0]
    assert t["id"] == "old" and t["title"] == "An old thread" and t["last_brain"] is None
    full = s.get_thread("old")
    assert [x["content"] for x in full["turns"]] == ["hi", "old reply"]
    assert all(x["brain"] is None for x in full["turns"])


def test_migration_is_idempotent(db):
    _old_db(db)
    ThreadStore(db)
    s = ThreadStore(db)                       # second open: nothing to add, must not fail
    assert s.usable
    assert s.record_turn("old", "again", "ok", brain={"provider": "claude", "model": ""})


def test_explicit_brain_is_recorded_on_the_turn_and_as_last_brain(db):
    s = ThreadStore(db)
    s.record_turn("c1", "hi", "hello", brain={"provider": "codex", "model": "gpt-5.6-terra"})
    assert s.list_threads()[0]["last_brain"] == {"provider": "codex", "model": "gpt-5.6-terra"}
    turns = s.get_thread("c1")["turns"]
    assert turns[0]["brain"] is None                                      # the user's row
    assert turns[1]["brain"] == {"provider": "codex", "model": "gpt-5.6-terra"}


def test_a_default_brain_turn_records_no_brain(db):
    s = ThreadStore(db)
    s.record_turn("c1", "hi", "hello")
    assert s.list_threads()[0]["last_brain"] is None
    assert s.get_thread("c1")["turns"][1]["brain"] is None


def test_switching_mid_thread_keeps_each_turns_brain_and_moves_last_brain(db):
    s = ThreadStore(db)
    s.record_turn("c1", "one", "a", brain={"provider": "claude", "model": "claude-sonnet-5"})
    s.record_turn("c1", "two", "b", brain={"provider": "gemini", "model": "gemini-3.7-flash-high"})
    assert s.get_thread("c1")["last_brain"] == {"provider": "gemini", "model": "gemini-3.7-flash-high"}
    brains = [t["brain"] for t in s.get_thread("c1")["turns"] if t["role"] == "assistant"]
    assert brains == [{"provider": "claude", "model": "claude-sonnet-5"},
                      {"provider": "gemini", "model": "gemini-3.7-flash-high"}]


def test_going_back_to_the_default_clears_last_brain(db):
    s = ThreadStore(db)
    s.record_turn("c1", "one", "a", brain={"provider": "claude", "model": ""})
    s.record_turn("c1", "two", "b")
    assert s.get_thread("c1")["last_brain"] is None
