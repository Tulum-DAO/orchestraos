"""RED-first — facts-only recall path for a clean install (gm commission msg_5f23d0bf).

On a fresh checkout the only facts source that exists is the dashboard's store:
`$ORCHESTRA_DIR/facts/facts_db.json`, written by `POST /api/facts`. The private
tree's `state/brain/facts.db` (a nightly ingest sqlite) never exists there, so
recall was inert. This module makes facts_recall read the JSON store with NO
private imports (no scripts.brain, no worldview tables) and merge it with the
sqlite store when that happens to exist.

Acceptance shape (gate step 7): write a fact -> restart -> the FACTS block
carries it on the next turn.
"""
import inspect
import json
import os
import sqlite3
import time

import pytest

from services.arturo import facts_recall as fr


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    fr._reset_for_tests()
    monkeypatch.setenv("ARTURO_FACTS_RECALL", "1")
    yield
    fr._reset_for_tests()


def _write_store(root, facts):
    d = root / "facts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "facts_db.json"
    p.write_text(json.dumps({"facts": facts, "last_updated": "2026-09-17T00:00:00Z"}))
    return str(p)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


# --- no private imports -------------------------------------------------------

def test_module_has_no_private_imports():
    src = inspect.getsource(fr)
    assert "scripts.brain" not in src, "public facts_recall must not import the private brain"
    assert "worldview" not in src.lower(), "public facts_recall is facts-only (no L2 worldview)"
    assert not hasattr(fr, "query_worldview")


# --- JSON store source --------------------------------------------------------

def test_json_store_fact_surfaces_in_block(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    _write_store(tmp_path, [
        {"id": 1, "fact": "The demo pixel for Acme Dental went live on the staging site",
         "source": "dashboard", "timestamp": _now(), "verified_at": _now()},
    ])
    block = fr.facts_preamble("what's going on with the acme dental pixel")
    assert "Acme Dental" in block
    assert block.startswith("FACTS")


def test_json_store_missing_is_inert(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    assert fr.facts_preamble("what's going on with the acme dental pixel") == ""


def test_json_store_corrupt_is_inert_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    (tmp_path / "facts").mkdir()
    (tmp_path / "facts" / "facts_db.json").write_text("{not json")
    assert fr.facts_preamble("what's going on with the acme dental pixel") == ""


def test_json_store_accepts_text_or_fact_field(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    _write_store(tmp_path, [
        {"id": 1, "text": "Acme Dental signed the retainer on Tuesday", "timestamp": _now()},
    ])
    assert "retainer" in fr.facts_preamble("tell me about acme dental")


def test_json_store_noise_filtered(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    _write_store(tmp_path, [
        {"id": 1, "fact": "You are a goal-extraction agent. Acme Dental ...", "timestamp": _now()},
        {"id": 2, "fact": "Acme Dental prefers Thursday check-in calls", "timestamp": _now()},
    ])
    block = fr.facts_preamble("what do we know about acme dental")
    assert "Thursday" in block
    assert "goal-extraction" not in block


def test_json_store_newest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    _write_store(tmp_path, [
        {"id": 1, "fact": "Acme Dental old note alpha", "timestamp": "2026-01-01T00:00:00Z"},
        {"id": 2, "fact": "Acme Dental new note beta", "timestamp": _now()},
    ])
    block = fr.facts_preamble("what do we know about acme dental")
    assert block.index("beta") < block.index("alpha")


def test_json_store_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    p = _write_store(tmp_path, [{"id": 1, "fact": "Acme Dental likes blue", "timestamp": _now()}])
    before = os.stat(p).st_mtime_ns
    fr.facts_preamble("what do we know about acme dental")
    assert os.stat(p).st_mtime_ns == before


# --- merge with the sqlite store when it exists --------------------------------

def _make_sqlite(path):
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE facts (
            fact_id TEXT PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL,
            text TEXT NOT NULL, actor TEXT NOT NULL, confidence REAL NOT NULL,
            entities TEXT NOT NULL, lanes TEXT NOT NULL, provenance TEXT NOT NULL,
            state TEXT NOT NULL, expiry_class TEXT NOT NULL, ingested_at TEXT NOT NULL)""")
    now = _now()
    con.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("f1", now, "fact", "Acme Dental sqlite-side fact about invoices",
                 "operator", 1.0, "{}", "[]", "{}", "active", "durable", now))
    con.commit(); con.close()


def test_both_sources_merge(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    (tmp_path / "state" / "brain").mkdir(parents=True)
    _make_sqlite(str(tmp_path / "state" / "brain" / "facts.db"))
    _write_store(tmp_path, [{"id": 1, "fact": "Acme Dental json-side fact about pixels",
                             "timestamp": _now()}])
    block = fr.facts_preamble("what do we know about acme dental")
    assert "invoices" in block and "pixels" in block


# --- the knowledge tool reads the same merged sources --------------------------

def test_query_sources_is_the_shared_entrypoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    _write_store(tmp_path, [{"id": 1, "fact": "Acme Dental json-side fact about pixels",
                             "timestamp": _now()}])
    rows = fr.query_sources(["acme", "dental"], k=4)
    assert rows and any("pixels" in r["text"] for r in rows)
    assert all({"text", "ts", "kind"} <= set(r) for r in rows)
