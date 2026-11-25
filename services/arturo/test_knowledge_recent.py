"""The past day's work reaches a call (2026-11-25: Arturo could not say what happened last night).

Two causes, both pinned here:
- native unset ARTURO_FACTS_RECALL because that flag also starts the proxy's cache-warm thread, so the knowledge tool
  never read the facts store on a call. ARTURO_KNOWLEDGE_FACTS=1 now lets the knowledge tool read it alone.
- a time question was scored as keywords. Time words now make a window, newest first, every line stamped.
And a recent_work(hours) tool: the window's facts, the repos' commits and the calls, newest first (fleet only: it
runs git).
"""
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from services.arturo import facts_recall as fr
from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture (ARTURO_STATE in tmp)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    p = tmp_path / "facts.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE facts (fact_id TEXT, ts TEXT, kind TEXT, text TEXT, expiry_class TEXT, "
                "confidence REAL, state TEXT)")
    now = datetime.now(timezone.utc)
    for fid, ago, text in [("f1", timedelta(days=5), "nod nod nod nod old plan"),
                           ("f2", timedelta(hours=10), "nod: dream collector cites the real author"),
                           ("f3", timedelta(hours=2), "facts ingest ran clean")]:
        con.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?)",
                    (fid, (now - ago).isoformat().replace("+00:00", "Z"), "decision", text, "standard", 1.0, "active"))
    con.commit()
    con.close()
    import sys
    monkeypatch.setitem(sys.modules, "facts_recall", fr)    # the proxy also imports it by its bare name
    monkeypatch.setattr(fr, "default_db_path", lambda: str(p))
    monkeypatch.setattr(fr, "default_json_path", lambda: str(tmp_path / "none.json"))
    monkeypatch.delenv("ARTURO_FACTS_RECALL", raising=False)
    monkeypatch.delenv("ARTURO_KNOWLEDGE_FACTS", raising=False)
    return p


def test_without_either_flag_the_store_is_not_read(P, store):
    out = P._knowledge_lookup("what happened last night")
    assert "dream collector" not in out


def test_the_knowledge_flag_alone_reads_the_window_newest_first(P, store, monkeypatch):
    monkeypatch.setenv("ARTURO_KNOWLEDGE_FACTS", "1")
    out = P._knowledge_lookup("what happened last night")
    assert "ingest ran clean" in out and "dream collector" in out
    assert out.index("ingest ran clean") < out.index("dream collector")    # newest first
    assert "old plan" not in out                                          # outside the window
    assert "Z" in out.split("ingest ran clean")[0].splitlines()[-1]       # the line carries its time


def test_the_knowledge_flag_alone_also_blends_topic_facts(P, store, monkeypatch):
    monkeypatch.setenv("ARTURO_KNOWLEDGE_FACTS", "1")
    out = P._knowledge_lookup("nod")
    assert "BRAIN FACTS" in out


def test_the_knowledge_flag_starts_no_thread():
    src = open("services/arturo/arturo-proxy.py").read()
    warm = src[src.index("def _warm_facts_cache"):src.index('log.info("facts cache warm started")')]
    assert "ARTURO_KNOWLEDGE_FACTS" not in warm


def test_recent_work_lists_facts_commits_and_calls_newest_first(P, store, monkeypatch, tmp_path):
    calls = tmp_path / "calls"
    calls.mkdir()
    t = time.time() - 3600
    (calls / "vc_x1.json").write_text(json.dumps({"call_id": "vc_x1", "started_at": t, "ended_at": t + 60,
                                                  "status": "ended", "turns": [{"role": "user", "text": "hi", "ts": t}]}))
    monkeypatch.setattr(P, "VOICE_CALLS_DIR", calls)
    monkeypatch.setattr(P, "_git_since", lambda repo, hours: [("2026-11-25T17:00:00Z", "abc1234", "fix(x): a thing")])
    out = P._as_turn({"principal": "fleet"}, P.execute_tool, "recent_work", {"hours": 24})
    assert "ingest ran clean" in out and "fix(x): a thing" in out and "vc_x1" in out
    assert "old plan" not in out


def test_recent_work_is_not_offered_to_an_unverified_caller(P):
    assert "recent_work" not in P._NON_FLEET_ALLOWED
    out = P._as_turn({"principal": "device:x"}, P.execute_tool, "recent_work", {"hours": 24})
    assert "ingest" not in out
