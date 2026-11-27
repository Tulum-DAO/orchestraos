"""Plan step 1: hybrid facts recall (FTS5 bm25 + MiniLM vectors, rank-fused) behind facts_recall.query_sources.

The real model is not shipped to CI, so the embedder here is a fake with the property that matters: two texts
that use different words for the same thing ("car" / "automobile") get the same vector, and unrelated texts get
orthogonal ones. Everything else (the sidecar index, sync, the fusion, the live-row filters) is the real code.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time

import numpy as np
import pytest

from services.arturo import facts_index as fi
from services.arturo import facts_recall as fr

# words that mean the same thing share one concept, so a rewording embeds like the original
_CONCEPT = {"automobile": "car", "vehicle": "car", "motor": "car", "repaired": "fix", "repair": "fix",
            "fixed": "fix", "mechanic": "fix", "garage": "fix", "invoice": "bill", "billing": "bill",
            "invoices": "bill", "paid": "bill", "payment": "bill"}


def _vec(text):
    v = np.zeros(fi.DIM, dtype=np.float32)
    for w in text.lower().replace(".", " ").replace(",", " ").split():
        w = _CONCEPT.get(w, w)
        if len(w) < 3 or w in fi._STOP:
            continue
        v[int(hashlib.sha1(w.encode()).hexdigest(), 16) % fi.DIM] += 1.0
    if not v.any():
        v[0] = 1.0
    return v / np.linalg.norm(v)


calls = []


def fake_embed(texts):
    calls.append(threading.current_thread().name)
    return [_vec(t) for t in texts]


NOW = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

ROWS = [
    # (fact_id, kind, text, state)
    ("f-car", "fact", "Shaw took the car to the garage on Tuesday and it is repaired now", "active"),
    ("f-bill", "decision", "Northwind invoice is paid in full, no balance left", "active"),
    ("f-old", "fact", "The automobile mechanic quoted four hundred dollars", "superseded"),
    ("f-noise", "fact", "Distill this recorded conversation about the automobile into facts", "active"),
    ("f-dog", "fact", "Luna the dog has a vet appointment next week", "active"),
]


def _make_db(path, rows=ROWS):
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE facts (
            fact_id TEXT PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL,
            text TEXT NOT NULL, actor TEXT NOT NULL, confidence REAL NOT NULL,
            entities TEXT NOT NULL, lanes TEXT NOT NULL, provenance TEXT NOT NULL,
            state TEXT NOT NULL, expiry_class TEXT NOT NULL, ingested_at TEXT NOT NULL)""")
    con.execute("CREATE INDEX idx_facts_ts ON facts(ts)")
    _add(con, rows)
    con.close()


def _add(con, rows):
    con.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(fid, NOW, kind, text, "shaw", 1.0, "{}", "[]", json.dumps({"source_kind": "voice"}),
                      state, "standard", NOW) for fid, kind, text, state in rows])
    con.commit()


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    (tmp_path / "state" / "brain").mkdir(parents=True)
    db = str(tmp_path / "state" / "brain" / "facts.db")
    _make_db(db)
    fi._reset_for_tests()
    calls.clear()

    def no_real_model(threads):
        raise ImportError("tests never load the real model")
    monkeypatch.setattr(fi, "fastembed_embedder", no_real_model)
    yield {"db": db, "index": fi.default_index_path(), "json": str(tmp_path / "facts" / "facts_db.json")}
    fi._reset_for_tests()


def _ready(index):
    """Run the loader to completion with the fake embedder (production does this on a daemon thread)."""
    assert fi.search("warm", index_path=index, embed_factory=lambda: fake_embed) is None
    for _ in range(200):
        if fi.ready(index):
            return
        time.sleep(0.01)
    raise AssertionError(f"loader never became ready: {fi._state}")


def _ids(facts):
    return [f["fact_id"] for f in facts]


# --- the fallback is today's answer, byte for byte ----------------------------------------------------------

def test_flag_off_is_exactly_keyword_recall(store, monkeypatch):
    """Even with the index built and loaded, the flag off means today's ranking."""
    monkeypatch.setenv(fi.FLAG, "1")
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    q = "did the mechanic fix my automobile"
    kws = fr.extract_keywords(q)
    assert fr.query_sources(kws, text=q) != fr._query_sources_keyword(kws)      # the hybrid would answer differently
    monkeypatch.delenv(fi.FLAG)
    n = len(calls)
    assert fr.query_sources(kws, text=q) == fr._query_sources_keyword(kws)
    assert len(calls) == n                                                      # and nothing was embedded


def test_flag_on_without_index_is_exactly_keyword_recall(store, monkeypatch):
    monkeypatch.setenv(fi.FLAG, "1")
    kws = fr.extract_keywords("what about the northwind invoice")
    assert not os.path.exists(store["index"])
    assert fr.query_sources(kws, text="what about the northwind invoice") == fr._query_sources_keyword(kws)


def test_flag_on_before_the_loader_finishes_is_keyword_recall(store, monkeypatch):
    """The model loads on a background thread; a turn that arrives first gets the keyword answer at once."""
    monkeypatch.setenv(fi.FLAG, "1")
    fi.sync(store["db"], store["index"], embed=fake_embed)
    gate = threading.Event()

    def slow_factory():
        gate.wait(5)
        return fake_embed
    kws = fr.extract_keywords("is the car fixed")
    t0 = time.perf_counter()
    assert fi.search("is the car fixed", index_path=store["index"], embed_factory=slow_factory) is None
    assert fr.query_sources(kws, text="is the car fixed") == fr._query_sources_keyword(kws)
    assert time.perf_counter() - t0 < 1.0
    gate.set()


# --- what the hybrid adds -----------------------------------------------------------------------------------

def test_hybrid_finds_a_fact_told_in_other_words(store, monkeypatch):
    monkeypatch.setenv(fi.FLAG, "1")
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    q = "did the mechanic fix my automobile"
    kws = fr.extract_keywords(q)
    assert "f-car" not in _ids(fr._query_sources_keyword(kws))        # no shared word: keyword recall misses it
    got = _ids(fr.query_sources(kws, text=q))
    assert got[0] == "f-car"


def test_hybrid_keeps_what_keyword_recall_finds(store, monkeypatch):
    monkeypatch.setenv(fi.FLAG, "1")
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    q = "what about the northwind invoice"
    kws = fr.extract_keywords(q)
    assert _ids(fr._query_sources_keyword(kws))[0] == "f-bill"
    assert _ids(fr.query_sources(kws, text=q))[0] == "f-bill"


def test_hybrid_never_serves_superseded_or_noise_rows(store, monkeypatch):
    """Both rows are in the index and are close in meaning to the question; the live row decides."""
    monkeypatch.setenv(fi.FLAG, "1")
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    q = "automobile mechanic quoted dollars"
    fts, vec = fi.search(q, index_path=store["index"])
    assert "f-old" in fts + vec and "f-noise" in fts + vec
    got = _ids(fr.query_sources(fr.extract_keywords(q), text=q))
    assert "f-old" not in got and "f-noise" not in got


def test_a_fact_superseded_after_indexing_stops_being_served(store, monkeypatch):
    monkeypatch.setenv(fi.FLAG, "1")
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    q = "did the mechanic fix my automobile"
    assert "f-car" in _ids(fr.query_sources(fr.extract_keywords(q), text=q))
    con = sqlite3.connect(store["db"])
    con.execute("UPDATE facts SET state='superseded' WHERE fact_id='f-car'")
    con.commit()
    con.close()
    assert "f-car" not in _ids(fr.query_sources(fr.extract_keywords(q), text=q))


def test_unrelated_question_gets_no_vector_filler(store, monkeypatch):
    monkeypatch.setenv(fi.FLAG, "1")
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    fts, vec = fi.search("quantum chromodynamics lattice", index_path=store["index"])
    assert fts == [] and vec == []
    assert fr.query_sources(["quantum", "chromodynamics"], text="quantum chromodynamics lattice") == []


def test_hybrid_includes_the_dashboard_json_store(store, monkeypatch):
    monkeypatch.setenv(fi.FLAG, "1")
    os.makedirs(os.path.dirname(store["json"]))
    with open(store["json"], "w") as fh:
        json.dump({"facts": [{"id": 7, "fact": "The zebra exhibit opens in March", "timestamp": NOW}]}, fh)
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    q = "when does the zebra exhibit open"
    assert _ids(fr.query_sources(fr.extract_keywords(q), text=q))[0] == "json:7"


# --- sync -----------------------------------------------------------------------------------------------------

def test_sync_is_incremental_and_picked_up_without_reload(store, monkeypatch):
    monkeypatch.setenv(fi.FLAG, "1")
    assert fi.sync(store["db"], store["index"], embed=fake_embed)["added"] == len(ROWS)
    _ready(store["index"])
    assert fi.sync(store["db"], store["index"], embed=fake_embed)["added"] == 0
    con = sqlite3.connect(store["db"])
    _add(con, [("f-boat", "fact", "The sailboat needs new sails before the regatta", "active")])
    con.close()
    out = fi.sync(store["db"], store["index"], embed=fake_embed)
    assert out["added"] == 1 and out["indexed"] == len(ROWS) + 1
    q = "sailboat regatta"
    assert _ids(fr.query_sources(fr.extract_keywords(q), text=q))[0] == "f-boat"


def test_sync_resumes_after_a_partial_run(store):
    assert fi.sync(store["db"], store["index"], embed=fake_embed, batch=2, max_rows=3)["indexed"] == 3
    out = fi.sync(store["db"], store["index"], embed=fake_embed, batch=2)
    assert out["added"] == len(ROWS) - 3 and out["indexed"] == len(ROWS)


def test_sync_rebuilds_when_the_store_is_replaced(store, tmp_path):
    fi.sync(store["db"], store["index"], embed=fake_embed)
    os.remove(store["db"])
    _make_db(store["db"], ROWS[:2])
    out = fi.sync(store["db"], store["index"], embed=fake_embed)
    assert out["rebuilt"] and out["indexed"] == 2


def test_a_reinserted_fact_replaces_its_old_entry(store):
    """Ingest never deletes or rewrites a fact today; if a store ever re-adds one under a new rowid, the index keeps
    one entry for it, with the new text."""
    fi.sync(store["db"], store["index"], embed=fake_embed)
    con = sqlite3.connect(store["db"])
    con.execute("DELETE FROM facts WHERE fact_id='f-car'")            # not the top rowid, so f-car comes back past it
    con.commit()
    _add(con, [("f-car", "fact", "The car is back from the garage since Friday", "active")])
    con.close()
    out = fi.sync(store["db"], store["index"], embed=fake_embed)
    assert out["added"] == 1 and out["indexed"] == len(ROWS)
    ix = sqlite3.connect(store["index"])
    assert ix.execute("SELECT COUNT(*) FROM fts WHERE fts MATCH 'friday'").fetchone()[0] == 1
    assert ix.execute("SELECT COUNT(*) FROM fts WHERE fts MATCH 'tuesday'").fetchone()[0] == 0
    assert ix.execute("SELECT COUNT(*) FROM doc WHERE fact_id='f-car'").fetchone()[0] == 1


def test_cli_sync_yields_the_cpu_and_caps_threads(store, monkeypatch):
    seen = {}
    monkeypatch.setattr(fi, "_yield_cpu", lambda: seen.setdefault("yield", True))

    def factory(threads):
        seen["threads"] = threads
        return fake_embed
    monkeypatch.setattr(fi, "fastembed_embedder", factory)
    assert fi.main(["sync", "--facts", store["db"], "--index", store["index"], "--threads", "8"]) == 0
    assert seen == {"yield": True, "threads": 2}
    assert fi.status(store["db"], store["index"])["indexed"] == len(ROWS)


def test_yield_cpu_lowers_priority_and_pins_cores():
    """Run in a child so the test process keeps its own priority and cores."""
    import subprocess
    import sys
    code = ("import os; from services.arturo import facts_index as fi; fi._yield_cpu(); "
            "print(os.nice(0), sorted(os.sched_getaffinity(0)))")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True).stdout
    nice, cores = out.split(" ", 1)
    assert int(nice) == 19
    want = sorted(set(fi.HEAVY_CORES) & set(range(os.cpu_count() or 1)))
    if want and set(want) <= os.sched_getaffinity(0):
        assert cores.strip() == str(want)


def test_fts_match_quotes_every_term():
    assert fi.fts_match('what is "NEAR" the AND-gate?') == '"near" OR "gate"'
    assert fi.fts_match("what is it") == ""
