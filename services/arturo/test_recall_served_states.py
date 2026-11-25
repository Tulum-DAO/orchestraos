"""Recall never serves a fact the brain has not let stand: superseded, archived and unverified rows stay out.

'unverified' is a fact that claims the operator's words without a quote that checks out; it is kept, but no
recall path may serve it. 'archived' is a fact that decayed or was set aside; it is served like superseded:
not at all. Synthetic databases only.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "scripts", "brain"))

from services.arturo import facts_recall as fr  # noqa: E402
from services.recall import core  # noqa: E402
from services.recall import worldview_recall as wv  # noqa: E402
import facts_schema  # noqa: E402
import lane_context  # noqa: E402

STATES = ("active", "resolved", "expired", "superseded", "archived", "unverified")
SERVED = {"active", "resolved", "expired"}


def _facts_db(path, now="2026-11-25T10:00:00Z"):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE facts (fact_id TEXT PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
            actor TEXT NOT NULL, confidence REAL NOT NULL, entities TEXT NOT NULL, lanes TEXT NOT NULL,
            provenance TEXT NOT NULL, state TEXT NOT NULL, expiry_class TEXT NOT NULL, ingested_at TEXT NOT NULL,
            superseded_by TEXT, slot_key TEXT, valid_from TEXT, valid_to TEXT);
        CREATE TABLE ingest_meta (source_kind TEXT PRIMARY KEY, last_source_event_ts TEXT, last_ingest_at TEXT NOT NULL);
        CREATE TABLE statements (statement_id TEXT PRIMARY KEY, source_type TEXT, source_ref TEXT, author TEXT,
            ts TEXT, topic TEXT, content TEXT, metadata TEXT);
        CREATE TABLE beliefs (belief_id TEXT PRIMARY KEY, subject TEXT, claim TEXT, current_value TEXT,
            confidence REAL, tier TEXT, derived_from TEXT, last_updated TEXT, superseded_by TEXT,
            history TEXT DEFAULT '[]', facet TEXT);
    """)
    for i, st in enumerate(STATES):
        con.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"f_{st}", now, "fact", f"marigold rollout note {st}", "shaw", 1.0, "{}", '["ops"]',
                     json.dumps({"source_kind": "dream-facts"}), st, "standard", now, None,
                     "shaw.location", now, None))
        con.execute("INSERT INTO statements VALUES (?,?,?,?,?,?,?,?)",
                    (f"s_{st}", "dream-facts", "{}", "shaw", f"2026-11-2{i}T00:00:00Z", "t",
                     f"marigold {st} is up", json.dumps({"expiry_class": "ephemeral", "state": st})))
        con.execute("INSERT INTO beliefs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (f"b_{st}", f"marigold {st}", "c", f"marigold {st} is healthy", 0.9, "standard",
                     json.dumps([f"s_{st}"]), now, None, "[]", None))
    con.commit()
    con.close()
    return str(path)


def _state_of(text):
    return next(st for st in STATES if f" {st}" in f" {text} ")


def test_the_rule_is_one_rule():
    assert set(fr.UNSERVED_STATES) == {"superseded", "archived", "unverified"}
    assert set(facts_schema.UNSERVED_STATES) == set(fr.UNSERVED_STATES)
    assert {"unverified", "archived"} <= facts_schema.STATES
    assert {st for st in STATES if fr.served(st)} == SERVED
    assert fr.served(None) and fr.served("")                     # rows from before the state column


def test_facts_query_serves_only_served_states(tmp_path):
    db = _facts_db(tmp_path / "f.db")
    got = {_state_of(f["text"]) for f in fr.query_facts(db, ["marigold"], k=20)}
    assert got == SERVED


def test_recent_facts_serves_only_served_states(tmp_path):
    db = _facts_db(tmp_path / "f.db")
    got = {_state_of(f["text"]) for f in fr.recent_facts(24 * 365, ["marigold"], k=20, dbpath=db,
                                                         now=datetime(2026, 11, 25, 11, tzinfo=timezone.utc))}
    assert got == SERVED


def test_json_store_serves_only_served_states(tmp_path):
    p = tmp_path / "facts.json"
    p.write_text(json.dumps({"facts": [{"ts": "2026-11-25T10:00:00Z", "kind": "fact", "state": st,
                                        "text": f"marigold rollout note {st}"} for st in STATES]}))
    got = {_state_of(f["text"]) for f in fr.query_facts_json(str(p), ["marigold"], k=20)}
    assert got == SERVED


def test_worldview_skips_statements_and_beliefs_resting_on_unserved_facts(tmp_path):
    db = _facts_db(tmp_path / "f.db")
    got = wv.query_worldview(db, ["marigold"], k=20)
    beliefs = {_state_of(e["text"]) for e in got if e["kind"] == "belief"}
    fluid = {_state_of(e["text"]) for e in got if e["kind"] == "fluid"}
    assert beliefs == SERVED
    assert fluid == SERVED


def test_slot_view_serves_only_served_states(tmp_path):
    db = _facts_db(tmp_path / "f.db")
    con = sqlite3.connect(db)
    con.execute("UPDATE facts SET valid_from = ts")
    con.commit()
    con.close()
    got = core.open_slot_facts(db, "shaw.location")
    assert got and {_state_of(f["text"]) for f in got} <= SERVED


def test_lane_context_serves_only_served_states(tmp_path):
    db = _facts_db(tmp_path / "f.db")
    got = {_state_of(f["text"]) for f in lane_context.get_context("ops", db_path=db)["facts"]}
    assert got == SERVED


@pytest.mark.parametrize("st", ["archived", "unverified"])
def test_an_unserved_row_alone_yields_nothing(tmp_path, st):
    db = _facts_db(tmp_path / "f.db")
    con = sqlite3.connect(db)
    con.execute("DELETE FROM facts WHERE state != ?", (st,))
    con.commit()
    con.close()
    assert fr.query_facts(db, ["marigold"], k=5) == []
    assert core.open_slot_facts(db, "shaw.location") == []
