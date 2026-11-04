"""questionnaire_schema.migrate() when another process adds a column between our PRAGMA and our ALTER: SQLite answers
"duplicate column name", which means the column is there, so migrate() carries on (and does not backfill twice).
Any other ALTER error still raises."""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import questionnaire_schema as QS
from questionnaire_schema import QuestionnaireStore


def _cols(db):
    c = sqlite3.connect(db)
    try:
        return {r[1] for r in c.execute("PRAGMA table_info(questionnaires)")}
    finally:
        c.close()


def test_add_column_is_true_once_then_false_on_a_duplicate(tmp_path):
    c = sqlite3.connect(str(tmp_path / "x.db"))
    c.execute("CREATE TABLE questionnaires (id TEXT)")
    assert QS._add_column(c, "ALTER TABLE questionnaires ADD COLUMN notify_skipped TEXT") is True
    assert QS._add_column(c, "ALTER TABLE questionnaires ADD COLUMN notify_skipped TEXT") is False
    with pytest.raises(sqlite3.OperationalError):
        QS._add_column(c, "ALTER TABLE no_such_table ADD COLUMN x TEXT")


def test_migrate_survives_another_process_adding_every_column_first(tmp_path, monkeypatch):
    db = str(tmp_path / "q.db")
    QuestionnaireStore(db_path=db).migrate()
    full = _cols(db)
    assert "notify_skipped" in full
    # A fresh table, then a migrate whose PRAGMA is answered as if nothing were there yet while every ALTER finds
    # the column already added (the race): it must finish, and every column must be there exactly once.
    db2 = str(tmp_path / "q2.db")
    QuestionnaireStore(db_path=db2).migrate()
    real_conn = QuestionnaireStore._conn

    class Racing:
        def __init__(self, c):
            self.c = c

        def execute(self, sql, *a):
            if sql.startswith("PRAGMA table_info(questionnaires)"):
                return self.c.execute("SELECT 'cid', 'id' WHERE 0")        # "no extra columns yet"
            if sql.startswith("UPDATE questionnaires SET real_attempts"):
                raise AssertionError("backfilled although another process added real_attempts")
            return self.c.execute(sql, *a)

        def __getattr__(self, k):
            return getattr(self.c, k)

    monkeypatch.setattr(QuestionnaireStore, "_conn", lambda self: Racing(real_conn(self)))
    QuestionnaireStore(db_path=db2).migrate()
    assert _cols(db2) == full
