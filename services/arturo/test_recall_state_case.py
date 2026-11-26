"""A fact's state is read the same way whatever its case and surrounding whitespace.

'Superseded', 'ARCHIVED' or ' superseded ' is never served; 'Unverified' or ' UNVERIFIED ' is served, flagged and
ranked below sourced facts, on every read path. A state is compared lowercased with its surrounding whitespace
removed, in SQL exactly as in Python. Synthetic databases only.
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

FLAG = "no source quote found"
SLOT = "ops.status"
# never served, however written
HIDDEN = ("Superseded", "SUPERSEDED", " superseded ", "\tArchived\n", "ARCHIVED", " archived　")
# served, flagged, ranked below sourced facts
UNSOURCED = ("Unverified", "UNVERIFIED", " unverified ", "\tUnVerified\n")


def _entries():
    """(label, state, ts): the sourced row is the OLDEST, so every hidden or unsourced row would outrank it."""
    out = [("sourced", "active", "2026-11-20T10:00:00Z")]
    out += [(f"hidden{i}", st, f"2026-11-25T1{i}:00:00Z") for i, st in enumerate(HIDDEN)]
    out += [(f"unsourced{i}", st, f"2026-11-24T1{i}:00:00Z") for i, st in enumerate(UNSOURCED)]
    return out


def _db(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE facts (fact_id TEXT PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
            actor TEXT NOT NULL, confidence REAL NOT NULL, entities TEXT NOT NULL, lanes TEXT NOT NULL,
            provenance TEXT NOT NULL, state TEXT NOT NULL, expiry_class TEXT NOT NULL, ingested_at TEXT NOT NULL,
            superseded_by TEXT, slot_key TEXT, valid_from TEXT, valid_to TEXT);
        CREATE INDEX idx_facts_ts ON facts(ts);
        CREATE TABLE ingest_meta (source_kind TEXT PRIMARY KEY, last_source_event_ts TEXT, last_ingest_at TEXT NOT NULL);
        CREATE TABLE statements (statement_id TEXT PRIMARY KEY, source_type TEXT, source_ref TEXT, author TEXT,
            ts TEXT, topic TEXT, content TEXT, metadata TEXT);
        CREATE TABLE beliefs (belief_id TEXT PRIMARY KEY, subject TEXT, claim TEXT, current_value TEXT,
            confidence REAL, tier TEXT, derived_from TEXT, last_updated TEXT, superseded_by TEXT,
            history TEXT DEFAULT '[]', facet TEXT);
    """)
    for label, st, ts in _entries():
        con.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"f_{label}", ts, "fact", f"marigold rollout note {label}", "operator", 1.0, "{}", '["ops"]',
                     json.dumps({"source_kind": "dream-facts"}), st, "durable", ts, None, SLOT, ts, None))
        con.execute("INSERT INTO statements VALUES (?,?,?,?,?,?,?,?)",
                    (f"s_{label}", "dream-facts", "{}", "operator", ts, "t", f"marigold {label} is up",
                     json.dumps({"expiry_class": "ephemeral", "state": st})))
        con.execute("INSERT INTO statements VALUES (?,?,?,?,?,?,?,?)",
                    (f"w_{label}", "wal", "{}", "operator", ts, "t", f"marigold wal {label}",
                     json.dumps({"anchor": {"seq": 7}, "expiry_class": "standard", "confidence": 0.5, "state": st})))
        # the sourced belief has the LOWEST confidence
        con.execute("INSERT INTO beliefs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (f"b_{label}", f"marigold belief {label}", "c", f"marigold belief {label}",
                     0.5 if label == "sourced" else 0.9, "standard", json.dumps([f"s_{label}"]), ts, None, "[]",
                     None))
    con.execute("INSERT INTO ingest_meta VALUES ('dream-facts', '2026-11-25T00:00:00Z', '2026-11-25T00:00:00Z')")
    con.commit()
    con.close()
    return str(path)


def _label(text):
    for word in (text or "").split():
        if word.startswith(("sourced", "hidden", "unsourced")):
            return word
    raise AssertionError(text)


def _check(entries):
    """Nothing hidden; each entry flagged iff unsourced; every unsourced entry after every sourced one. Returns the
    kinds of label seen."""
    labels = [_label(e["text"]) for e in entries]
    assert not [lb for lb in labels if lb.startswith("hidden")], labels
    assert [bool(e.get("unsourced")) for e in entries] == [lb.startswith("unsourced") for lb in labels], labels
    flags = [lb.startswith("unsourced") for lb in labels]
    assert flags == sorted(flags), labels
    return {lb.rstrip("0123456789") for lb in labels}


def test_states_read_without_case_or_surrounding_whitespace():
    for mod_served, mod_unsourced in ((fr.served, fr.unsourced), (facts_schema.is_served, facts_schema.is_unsourced)):
        assert not any(mod_served(st) for st in HIDDEN)
        assert all(mod_served(st) and mod_unsourced(st) for st in UNSOURCED)
        assert mod_served(None) and mod_served("") and mod_served(" Active ")
        assert not mod_unsourced(None) and not mod_unsourced(" active ")
    # the states SQL decides by a plain comparison are the brain's states
    assert set(fr._SERVED_STATES) | set(fr.UNSERVED_STATES) == facts_schema.STATES


def test_query_facts(tmp_path):
    assert _check(fr.query_facts(_db(tmp_path / "f.db"), ["marigold"], k=20)) == {"sourced", "unsourced"}


def test_query_facts_durable_window_reads_past_unsourced_variants(tmp_path, monkeypatch):
    """Both windows hold one row: the newest rows are padded or mixed-case unverified ones."""
    monkeypatch.setattr(fr, "DURABLE_LIMIT", 1)
    got = fr.query_facts(_db(tmp_path / "f.db"), ["marigold"], k=20, candidate_limit=1)
    assert [(_label(f["text"]), f["unsourced"]) for f in got] == [("sourced", False)]


def test_recent_facts(tmp_path):
    got = fr.recent_facts(24 * 30, ["marigold"], k=20, dbpath=_db(tmp_path / "f.db"),
                          now=datetime(2026, 11, 26, tzinfo=timezone.utc))
    assert _check(got) == {"sourced", "unsourced"}


def test_query_facts_json(tmp_path):
    p = tmp_path / "facts.json"
    p.write_text(json.dumps({"facts": [{"id": label, "timestamp": ts, "state": st,
                                        "text": f"marigold rollout note {label}"} for label, st, ts in _entries()]}))
    assert _check(fr.query_facts_json(str(p), ["marigold"], k=20)) == {"sourced", "unsourced"}


@pytest.mark.parametrize("kind", ["belief", "fluid", "wal"])
def test_query_worldview(tmp_path, kind):
    got = wv.query_worldview(_db(tmp_path / "f.db"), ["marigold"], k=20)
    assert _check([e for e in got if e["kind"] == kind]) == {"sourced", "unsourced"}
    rendered = wv._render_worldview(got)
    assert "hidden" not in rendered
    assert all(FLAG in ln for ln in rendered.splitlines() if "unsourced" in ln)


def test_open_slot_facts(tmp_path):
    db = _db(tmp_path / "f.db")
    assert [(_label(f["text"]), f["unsourced"]) for f in core.open_slot_facts(db, SLOT)] == [
        ("sourced", False), ("unsourced3", True)]
    con = sqlite3.connect(db)
    con.execute("DELETE FROM facts WHERE fact_id = 'f_sourced'")
    con.commit()
    con.close()
    assert all(f["unsourced"] and "unsourced" in f["text"] for f in core.open_slot_facts(db, SLOT))


def test_core_answer_facts_and_worldview_blocks(tmp_path):
    db = _db(tmp_path / "f.db")
    # budgets far past the default wall: this test is about what is served, not about a loaded machine's latency
    rc = core.Recall(facts_db=db, sm_db=str(tmp_path / "absent-sm.db"), embedder=False, sweep_enabled=False,
                     budgets={"facts": 30000, "worldview": 30000, "recall": 30000})
    out = rc.answer("marigold rollout note status", k=20, vector=False)
    assert _check(out["blocks"]["facts"]) == {"sourced", "unsourced"}
    assert _check(out["blocks"]["worldview"]) == {"sourced", "unsourced"}
    assert "hidden" not in out["rendered"]
    assert all(FLAG in ln for ln in out["rendered"].splitlines() if "unsourced" in ln)


def test_lane_context(tmp_path):
    got = lane_context.get_context("ops", db_path=_db(tmp_path / "f.db"))["facts"]
    assert _check(got) == {"sourced", "unsourced"}


def test_lane_context_pins_an_active_decision_whatever_the_case(tmp_path):
    """limit=1: a newer sourced fact takes the place, and an older decision whose state is ' Active ' is pinned in."""
    db = _db(tmp_path / "f.db")
    con = sqlite3.connect(db)
    con.execute("DELETE FROM facts")
    for fid, ts, kind, st in [("s1", "2026-11-25T09:00:00Z", "fact", "active"),
                              ("d", "2026-11-20T08:00:00Z", "decision", " Active ")]:
        con.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (fid, ts, kind, f"marigold {fid}", "operator", 1.0, "{}", '["ops"]', "{}", st, "standard", ts,
                     None, None, None, None))
    con.commit()
    con.close()
    assert [f["fact_id"] for f in lane_context.get_context("ops", db_path=db, limit=1)["facts"]] == ["d"]


# --- SQL and Python read a state alike: the windows decide in SQL what Python decides afterwards --------------------

_ODD = ["active", "", "Superseded", " superseded", "superseded ", "\u0085archived ", "\x1cunverified\x1f",
        "unverified​", "un verified", "Karchived", "SUPERSEDED\n", "unverified.", "5", "None", "null"]


def test_the_sql_state_rule_is_the_python_one(tmp_path):
    """Every whitespace str.strip removes, and only that; ASCII case only (no other letter lowercases to ASCII in
    a state name); a blob, a number and NULL as before."""
    codes = [c for c in range(0x110000) if chr(c).isspace()]
    assert sorted(fr._STRIP_CODES) == codes
    values = _ODD + [chr(c) + "Unverified" + chr(c) for c in codes] + [b"superseded", b"unverified", 5, 0.0, None]
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE facts (i INTEGER, state)")
    con.executemany("INSERT INTO facts VALUES (?, ?)", enumerate(values))
    sql = {i: (bool(s), bool(u)) for i, s, u in con.execute(
        f"SELECT i, {fr.SERVED_SQL}, {fr.UNSOURCED_SQL} FROM facts")}
    py = {i: (fr.served(v), fr.unsourced(v)) for i, v in enumerate(values)}
    assert sql == py
    assert py[values.index(b"superseded")] == (True, False)           # a blob is never a state name, as before


@pytest.mark.parametrize("sourced_only", [False, True])
def test_the_sql_statement_state_rule_is_the_python_one(sourced_only):
    """A statement's metadata state: SQL keeps exactly the rows Python keeps. Metadata with an escape is always
    handed to Python, so SQL keeps it."""
    codes = [c for c in range(0x110000) if chr(c).isspace()]
    values = _ODD + [chr(c) + "Superseded" + chr(c) for c in codes] + [5, 0, True, None, ["superseded"], {"a": 1}]
    metas = [json.dumps({"expiry_class": "ephemeral", "state": v}, ensure_ascii=False) for v in values]
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE statements (i INTEGER, source_type TEXT, metadata TEXT)")
    con.executemany("INSERT INTO statements VALUES (?, 'dream-facts', ?)", enumerate(metas))
    kept = {r[0] for r in con.execute(f"SELECT i FROM statements WHERE {wv._servable_sql(con, False, sourced_only)}")}
    for i, meta in enumerate(metas):
        st = wv._statement_state(meta)
        want = fr.served(st) and not (sourced_only and fr.unsourced(st))
        assert (i in kept) == (want or "\\" in meta), (meta, want)


# --- write side: a state is stored lowercased, and nothing accepted before is refused now --------------------------

def test_make_fact_and_ingest_store_the_state_lowercased(tmp_path, monkeypatch):
    import bus
    redactor = tmp_path / "redact.py"
    redactor.write_text("def scrub_fact_text(t):\n    return t\n")
    monkeypatch.setenv("REDACT_SECRETS_PATH", str(redactor))
    kw = dict(ts="2026-11-25T10:00:00Z", kind="fact", actor="operator", provenance={"source_kind": "t"})
    assert facts_schema.make_fact(text="a", state=" Superseded ", **kw)["state"] == "superseded"
    for st in facts_schema.STATES:
        assert facts_schema.make_fact(text="b", state=st, **kw)["state"] == st
    with pytest.raises(ValueError):
        facts_schema.make_fact(text="c", state="gone", **kw)
    raw = facts_schema.make_fact(text="d", **kw)
    raw["state"] = "ARCHIVED"
    assert facts_schema.validate_fact(raw) == []
    db = str(tmp_path / "facts.db")
    bus.ingest(db, [raw, facts_schema.make_fact(text="e", state="unverified", **kw)], source_kind="t",
               last_source_event_ts=None)
    con = sqlite3.connect(db)
    assert sorted(r[0] for r in con.execute("SELECT state FROM facts")) == ["archived", "unverified"]
    con.close()
