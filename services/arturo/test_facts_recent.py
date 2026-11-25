"""'What happened last night / in the past day' is a TIME question, not a keyword one (2026-11-25: Arturo was asked
about the past day and the keyword scorer surfaced 11-18..11-20 rows; "day" matched everything, and the 11-25 rows
lost to older text that matched more words). Time words become a window; the window's facts come newest first.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from services.arturo import facts_recall as fr

NOW = datetime(2026, 11, 25, 17, 30, tzinfo=timezone.utc)


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "facts.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE facts (fact_id TEXT, ts TEXT, kind TEXT, text TEXT, expiry_class TEXT, "
                "confidence REAL, state TEXT)")
    rows = [
        ("f1", NOW - timedelta(days=6), "decision", "nod roadmap decision from last week, nod nod nod"),
        ("f2", NOW - timedelta(hours=11), "decision", "Nod: dream collector now cites the real author"),
        ("f3", NOW - timedelta(hours=3), "insight", "facts.db ingest ran clean at 13:30Z"),
        ("f4", NOW - timedelta(hours=2), "fact", "you are the dream agent, emit a json"),          # noise
        ("f5", NOW - timedelta(hours=1), "fact", "old wording replaced"),
    ]
    for fid, ts, kind, text in rows:
        con.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?)",
                    (fid, _iso(ts), kind, text, "standard", 1.0, "superseded" if fid == "f5" else "active"))
    con.commit()
    con.close()
    return str(p)


@pytest.mark.parametrize("text,hours", [
    ("what happened last night", 18), ("anything new since yesterday?", 36), ("the past day's work", 24),
    ("last 24 hours", 24), ("in the past 3 days", 72), ("past 6 hours", 6), ("this week so far", 168),
    ("what is the dream collector", None),
])
def test_time_words_become_a_window(text, hours):
    assert fr.time_window_hours(text) == hours


def test_time_words_are_not_search_terms():
    assert fr.topic_keywords("what did we do on nod last night") == ["nod"]
    assert fr.topic_keywords("what happened in the past day") == []


def test_recent_facts_are_newest_first_inside_the_window(db):
    got = fr.recent_facts(24, dbpath=db, now=NOW)
    assert [f["fact_id"] for f in got] == ["f3", "f2"]          # f1 too old, f4 noise, f5 superseded


def test_a_topic_inside_the_window_comes_first(db):
    got = fr.recent_facts(24, keywords=["nod"], dbpath=db, now=NOW)
    assert [f["fact_id"] for f in got] == ["f2", "f3"]


def test_no_store_is_an_empty_answer(tmp_path):
    assert fr.recent_facts(24, dbpath=str(tmp_path / "absent.db"), now=NOW) == []
