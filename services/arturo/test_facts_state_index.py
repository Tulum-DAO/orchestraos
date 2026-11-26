"""Recall's sourced windows read through an index on the state, whatever the share of unsourced facts.

A window fills with sourced facts first, so it reads the newest sourced rows and, when it has room, the newest
unsourced ones. With only the ts index, reading the sourced rows walks past every unsourced one, and when nearly
every row is unsourced that is nearly the whole table. The bus indexes the state expression the windows filter on
(facts_recall.UNSOURCED_SQL), with ts and with (expiry_class, ts), so each sourced read is an index search that
stops at its limit. A db made before the indexes gets them the next time the bus opens it. What a window returns is
the same with the indexes as without them. Synthetic databases only.
"""
import json
import os
import random
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "scripts", "brain"))

from services.arturo import facts_recall as fr  # noqa: E402

INDEXES = {"idx_facts_unsourced_ts": "ts", "idx_facts_unsourced_expiry_ts": "expiry_class, ts"}
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
KEYWORDS = (["marigold"], ["zinnia", "proxy"])

# The facts table and indexes as a db made before the state indexes has them. Its state may be NULL (rows from
# before the column was filled).
_OLD_SCHEMA = """
CREATE TABLE facts (fact_id TEXT PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
    actor TEXT NOT NULL, confidence REAL NOT NULL, entities TEXT NOT NULL, lanes TEXT NOT NULL,
    provenance TEXT NOT NULL, state TEXT, expiry_class TEXT NOT NULL, superseded_by TEXT, ingested_at TEXT NOT NULL);
CREATE INDEX idx_facts_ts ON facts(ts);
CREATE INDEX idx_facts_kind ON facts(kind);
CREATE INDEX idx_facts_expiry_ts ON facts(expiry_class, ts);
CREATE TABLE ingest_meta (source_kind TEXT PRIMARY KEY, last_source_event_ts TEXT, last_ingest_at TEXT NOT NULL);
"""
# however a state is written: sourced, unsourced, never served
SOURCED = ("active", "resolved", "expired", " Active ", "RESOLVED", "Expired\t", "", None)
UNSOURCED = ("unverified", "Unverified", " UNVERIFIED ", "\tunverified\n")
UNSERVED = ("superseded", "archived", "Superseded", " ARCHIVED ")


@pytest.fixture
def bus():
    import bus as mod  # noqa: PLC0415
    return mod


def _index_sql(con):
    return {name: sql for name, sql in con.execute("SELECT name, sql FROM sqlite_master WHERE type='index'")}


def _seed(path, unsourced_share, n=3000, seed=7):
    """An old-schema db of n facts, `unsourced_share` of them unsourced, every state spelling, some ts ties."""
    rng = random.Random(seed)
    con = sqlite3.connect(path)
    con.executescript(_OLD_SCHEMA)
    ts = NOW
    for i in range(n):
        if i % 40:                                    # every 40th row shares the previous row's ts
            ts -= timedelta(seconds=rng.randint(1, 600))
        roll = rng.random()
        if roll < 0.03:
            st = rng.choice(UNSERVED)
        elif roll < 0.03 + unsourced_share * 0.97:
            st = rng.choice(UNSOURCED)
        else:
            st = rng.choice(SOURCED)
        text = rng.choice(("marigold rollout note", "zinnia proxy status", "marigold zinnia proxy",
                           "you are the marigold distiller", "unrelated entry")) + f" {i}"
        stamp = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"f{i:05d}", stamp, rng.choice(("fact", "goal", "decision", "insight")), text, "operator",
                     round(rng.random(), 3), "{}", "[]", json.dumps({"source_kind": "t"}), st,
                     rng.choice(("durable", "durable", "standard", "ephemeral", "immutable")), None, stamp))
    con.commit()
    con.close()


def _pair(tmp_path, bus, share):
    """(without, with): the same seeded rows, the second given the state indexes by the bus's migration."""
    old = str(tmp_path / f"old{share}.db")
    _seed(old, share)
    new = str(tmp_path / f"new{share}.db")
    shutil.copy(old, new)
    con = sqlite3.connect(new)
    bus._migrate(con)
    con.commit()
    con.close()
    return old, new


class _Traced:
    """A read-only connection that records every facts query it runs."""

    def __init__(self, con, log):
        self._con, self._log = con, log

    def execute(self, sql, params=()):
        if " FROM facts" in sql:
            self._log.append((sql, list(params)))
        return self._con.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._con, name)


def _run(db, monkeypatch):
    """Every window and every answer recall makes from db, and the facts queries it ran."""
    log, windows = [], []
    connect, window = fr._connect_ro, fr._facts_window

    def recorded(*a, **kw):
        rows = window(*a, **kw)
        windows.append([tuple(r) for r in rows])
        return rows

    monkeypatch.setattr(fr, "_connect_ro", lambda p: _Traced(connect(p), log))
    monkeypatch.setattr(fr, "_facts_window", recorded)
    answers = [fr.query_facts(db, kws, k=10 ** 6) for kws in KEYWORDS]
    answers.append(fr.recent_facts(24 * 30, keywords=["marigold"], k=10 ** 6, dbpath=db, now=NOW))
    answers.append(fr.recent_facts(24 * 30, keywords=["marigold"], k=12, dbpath=db, now=NOW))
    monkeypatch.setattr(fr, "_connect_ro", connect)
    monkeypatch.setattr(fr, "_facts_window", window)
    return json.dumps(windows), json.dumps(answers), log


def _plans(db, log):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [(sql, [r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql, params)]) for sql, params in log]
    finally:
        con.close()


def test_the_bus_indexes_recalls_state_expression(bus):
    assert bus.UNSOURCED_SQL == fr.UNSOURCED_SQL
    assert set(INDEXES) == set(fr.UNSOURCED_INDEXES)


def test_the_schema_creates_the_state_indexes():
    import bus  # noqa: PLC0415
    con = sqlite3.connect(":memory:")
    con.executescript(bus._SCHEMA)
    have = _index_sql(con)
    for name, cols in INDEXES.items():
        assert have.get(name) == f"CREATE INDEX {name} ON facts({fr.UNSOURCED_SQL}, {cols})"


def test_migrate_adds_them_to_a_db_made_before_them(tmp_path, bus):
    db = str(tmp_path / "facts.db")
    _seed(db, 0.1, n=200)
    con = sqlite3.connect(db)
    rows = con.execute("SELECT * FROM facts ORDER BY rowid").fetchall()
    assert not set(INDEXES) & set(_index_sql(con))
    bus._migrate(con)
    con.commit()
    have = _index_sql(con)
    for name, cols in INDEXES.items():
        assert have.get(name) == f"CREATE INDEX {name} ON facts({fr.UNSOURCED_SQL}, {cols})"
    assert con.execute("SELECT * FROM facts ORDER BY rowid").fetchall() == rows     # no row or column changed
    bus._migrate(con)                                                               # idempotent
    con.close()


def test_a_bus_write_gives_an_old_db_the_indexes(tmp_path, bus, monkeypatch):
    redactor = tmp_path / "redact.py"
    redactor.write_text("def scrub_fact_text(t):\n    return t\n")
    monkeypatch.setenv("REDACT_SECRETS_PATH", str(redactor))
    db = str(tmp_path / "facts.db")
    _seed(db, 0.1, n=50)
    bus.ingest(db, [], source_kind="t", last_source_event_ts=None)
    con = sqlite3.connect(db)
    assert set(INDEXES) <= set(_index_sql(con))
    con.close()


@pytest.mark.parametrize("share", [0.1, 0.95])
def test_the_sourced_windows_search_the_state_index(tmp_path, bus, monkeypatch, share):
    _, db = _pair(tmp_path, bus, share)
    _, _, log = _run(db, monkeypatch)
    plans = _plans(db, log)
    sourced = [(sql, plan) for sql, plan in plans if fr.UNSOURCED_SQL in sql]
    # both query_facts windows (recency, durable) for each keyword set, and the bounded recent_facts window
    assert len(sourced) == 2 * len(KEYWORDS) + 1, plans
    for sql, plan in sourced:
        name = "idx_facts_unsourced_expiry_ts" if "expiry_class IN" in sql else "idx_facts_unsourced_ts"
        assert any(p.startswith(f"SEARCH facts USING INDEX {name} (<expr>=?") for p in plan), plan
        assert not any(p.startswith("SCAN facts") or "TEMP B-TREE" in p for p in plan), plan
    for _, plan in plans:                  # no window reads the whole table or sorts it
        assert all("USING INDEX" in p or "USING COVERING INDEX" in p for p in plan if p.startswith("SCAN facts")), plan
        assert not any("TEMP B-TREE" in p for p in plan), plan


@pytest.mark.parametrize("share", [0.0, 0.1, 0.95])
def test_every_window_is_the_same_with_the_indexes(tmp_path, bus, monkeypatch, share):
    old, new = _pair(tmp_path, bus, share)
    windows_old, answers_old, log_old = _run(old, monkeypatch)
    windows_new, answers_new, log_new = _run(new, monkeypatch)
    assert windows_new == windows_old
    assert answers_new == answers_old
    assert json.loads(answers_old)[0]                                   # not vacuous: there is something to serve
    assert (len(log_new) > len(KEYWORDS) * 2 + 2) == (share > 0)        # the sourced reads ran where there are any
