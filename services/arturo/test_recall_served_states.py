"""What recall serves, by fact state.

- superseded and archived: never served, by any path.
- unverified (a fact with no source quote found): SERVED, but flagged "no source quote found", ranked below
  sourced facts, and never rendered as anyone's words.
- everything else (active, resolved, expired, no state): served as before.
Synthetic databases only.
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
SERVED = {"active", "resolved", "expired", "unverified"}
FLAG = "no source quote found"


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
        # the unverified row is the NEWEST, so ranking it last is the rule at work, not the data's order
        ts = "2026-11-25T12:00:00Z" if st == "unverified" else now
        con.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"f_{st}", ts, "fact", f"marigold rollout note {st}", "shaw", 1.0, "{}", '["ops"]',
                     json.dumps({"source_kind": "dream-facts"}), st, "standard", ts, None,
                     "shaw.location", ts, None))
        con.execute("INSERT INTO statements VALUES (?,?,?,?,?,?,?,?)",
                    (f"s_{st}", "dream-facts", "{}", "shaw", f"2026-11-2{i}T00:00:00Z", "t",
                     f"marigold {st} is up", json.dumps({"expiry_class": "ephemeral", "state": st})))
        con.execute("INSERT INTO beliefs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (f"b_{st}", f"marigold {st}", "c", f"marigold {st} is healthy", 0.99 if st == "unverified"
                     else 0.9, "standard", json.dumps([f"s_{st}"]), now, None, "[]", None))
    con.commit()
    con.close()
    return str(path)


def _state_of(text):
    return next(st for st in STATES if f" {st}" in f" {text} ")


def _check(entries):
    """States returned, each entry flagged iff unverified, and every unverified entry after every sourced one."""
    states = [_state_of(e["text"]) for e in entries]
    assert all(bool(e.get("unsourced")) == (st == "unverified") for e, st in zip(entries, states))
    if "unverified" in states:
        assert states.index("unverified") == len(states) - 1, states
    return set(states)


def test_the_rule_is_one_rule():
    assert set(fr.UNSERVED_STATES) == {"superseded", "archived"}
    assert set(facts_schema.UNSERVED_STATES) == set(fr.UNSERVED_STATES)
    assert {"unverified", "archived"} <= facts_schema.STATES
    assert {st for st in STATES if fr.served(st)} == SERVED
    assert fr.served(None) and fr.served("")
    assert fr.unsourced("unverified") and not fr.unsourced("active") and not fr.unsourced(None)


def test_facts_query(tmp_path):
    assert _check(fr.query_facts(_facts_db(tmp_path / "f.db"), ["marigold"], k=20)) == SERVED


def test_recent_facts(tmp_path):
    got = fr.recent_facts(24 * 365, ["marigold"], k=20, dbpath=_facts_db(tmp_path / "f.db"),
                          now=datetime(2026, 11, 25, 13, tzinfo=timezone.utc))
    assert _check(got) == SERVED


def test_json_store(tmp_path):
    p = tmp_path / "facts.json"
    p.write_text(json.dumps({"facts": [{"ts": "2026-11-25T12:00:00Z" if st == "unverified" else
                                        "2026-11-25T10:00:00Z", "kind": "fact", "state": st,
                                        "text": f"marigold rollout note {st}"} for st in STATES]}))
    assert _check(fr.query_facts_json(str(p), ["marigold"], k=20)) == SERVED


def test_worldview(tmp_path):
    got = wv.query_worldview(_facts_db(tmp_path / "f.db"), ["marigold"], k=20)
    assert _check([e for e in got if e["kind"] == "belief"]) == SERVED
    assert _check([e for e in got if e["kind"] == "fluid"]) == SERVED


def test_slot_view(tmp_path):
    db = _facts_db(tmp_path / "f.db")
    con = sqlite3.connect(db)
    con.execute("DELETE FROM facts WHERE state NOT IN ('unverified', 'superseded', 'archived')")
    con.commit()
    con.close()
    got = core.open_slot_facts(db, "shaw.location")
    assert [(_state_of(f["text"]), f.get("unsourced")) for f in got] == [("unverified", True)]


def test_lane_context(tmp_path):
    assert _check(lane_context.get_context("ops", db_path=_facts_db(tmp_path / "f.db"))["facts"]) == SERVED


def test_renderers_flag_an_unsourced_fact_and_never_frame_it_as_words():
    line = fr._render([{"kind": "fact", "ts": "2026-11-25", "text": "Shaw prefers short replies", "unsourced": True}])
    assert FLAG in line
    assert "said" not in line.lower().replace("shaw prefers", "")
    assert FLAG not in fr._render([{"kind": "fact", "ts": "2026-11-25", "text": "the proxy is up"}])
    wl = wv._render_worldview([{"kind": "fluid", "tier": "ephemeral", "text": "the proxy is up", "ts": "2026-11-25",
                                "cite": "per dream-facts 2026-11-25", "conf": 1.0, "unsourced": True}])
    assert FLAG in wl


@pytest.mark.parametrize("hidden", ["superseded", "archived"])
def test_end_to_end_through_the_recall_core(tmp_path, hidden):
    """An unverified fact and its backfilled statement come back through core.answer, flagged in both blocks and
    never as anyone's words; a superseded or archived one comes back through neither block."""
    db = _facts_db(tmp_path / "f.db")
    rc = core.Recall(facts_db=db, sm_db=str(tmp_path / "absent-sm.db"), embedder=False, sweep_enabled=False)
    out = rc.answer("marigold rollout note status", vector=False)
    facts, world = out["blocks"]["facts"], out["blocks"]["worldview"]
    assert {_state_of(f["text"]) for f in facts} >= {"unverified"}
    assert [f.get("unsourced") for f in facts if _state_of(f["text"]) == "unverified"] == [True]
    assert any(e.get("unsourced") and "unverified" in e["text"] for e in world)
    assert not any(hidden in f["text"] for f in facts + world)
    rendered = out["rendered"]
    assert hidden not in rendered
    for line in rendered.splitlines():
        if "unverified" in line:
            assert FLAG in line, line
    assert " said" not in rendered.lower()
