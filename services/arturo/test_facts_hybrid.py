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
    monkeypatch.setattr(fi, "REFRESH_EVERY_S", 0.0)      # tests sync and search back to back

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
    fts, vec, _ = fi.search(q, index_path=store["index"])
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
    fts, vec, _ = fi.search("quantum chromodynamics lattice", index_path=store["index"])
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
    first = fi.sync(store["db"], store["index"], embed=fake_embed, batch=2, max_rows=3)
    assert first["indexed"] == 3 and not first["complete"]
    out = fi.sync(store["db"], store["index"], embed=fake_embed, batch=2)
    assert out["added"] == len(ROWS) - 3 and out["indexed"] == len(ROWS) and out["complete"]


def test_sync_walks_back_over_rows_the_store_lost(store):
    fi.sync(store["db"], store["index"], embed=fake_embed)
    os.remove(store["db"])
    _make_db(store["db"], ROWS[:2])                      # the same first two facts, the rest gone
    out = fi.sync(store["db"], store["index"], embed=fake_embed)
    assert out["removed"] == len(ROWS) - 2 and out["indexed"] == 2 and not out["rebuilt"] and out["complete"]


def test_sync_rebuilds_when_the_store_is_replaced(store):
    fi.sync(store["db"], store["index"], embed=fake_embed)
    os.remove(store["db"])
    _make_db(store["db"], [("g-" + fid, kind, text, state) for fid, kind, text, state in ROWS[:2]])
    out = fi.sync(store["db"], store["index"], embed=fake_embed)
    assert out["rebuilt"] and out["indexed"] == 2 and out["complete"]


def test_a_reused_top_rowid_is_reindexed(store):
    """grok first pass #3: SQLite hands a deleted top rowid to the next insert."""
    os.remove(store["db"])
    _make_db(store["db"], ROWS[:2])
    fi.sync(store["db"], store["index"], embed=fake_embed)
    con = sqlite3.connect(store["db"])
    con.execute("DELETE FROM facts WHERE fact_id='f-bill'")
    con.commit()
    _add(con, [("f-dog", "fact", "Luna the dog has a vet appointment next week", "active")])
    assert con.execute("SELECT rowid FROM facts WHERE fact_id='f-dog'").fetchone()[0] == 2
    con.close()
    out = fi.sync(store["db"], store["index"], embed=fake_embed)
    assert out["removed"] == 1 and out["added"] == 1
    ix = sqlite3.connect(store["index"])
    assert sorted(r[0] for r in ix.execute("SELECT fact_id FROM doc")) == ["f-car", "f-dog"]


def test_a_partial_index_serves_keyword_recall(store, monkeypatch):
    """A build from empty that has not caught up would miss facts keyword recall finds."""
    monkeypatch.setenv(fi.FLAG, "1")
    out = fi.sync(store["db"], store["index"], embed=fake_embed, max_rows=2)
    assert not out["complete"]
    assert fi.search("warm", index_path=store["index"], embed_factory=lambda: fake_embed) is None
    for _ in range(200):
        if fi.ready(store["index"]):
            break
        time.sleep(0.01)
    q = "what about the northwind invoice"
    kws = fr.extract_keywords(q)
    assert fi.search(q, index_path=store["index"]) is None
    assert fr.query_sources(kws, text=q) == fr._query_sources_keyword(kws)
    assert fi.sync(store["db"], store["index"], embed=fake_embed)["complete"]
    for _ in range(200):                                 # the loader refreshes in the background
        if fi.search(q, index_path=store["index"]) is not None:
            return
        time.sleep(0.01)
    raise AssertionError("the completed index was never served")


def test_an_emptied_index_serves_keyword_recall(store, monkeypatch):
    """grok first pass #2: rows deleted under a loaded matrix must not read as a finished, empty index."""
    monkeypatch.setenv(fi.FLAG, "1")
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    ix = sqlite3.connect(store["index"])
    ix.execute("DELETE FROM doc")
    ix.execute("DELETE FROM fts")
    ix.commit()
    ix.close()
    q = "what about the northwind invoice"
    kws = fr.extract_keywords(q)
    for _ in range(3):                                   # the first call hands the reload to the loader
        assert fr.query_sources(kws, text=q) == fr._query_sources_keyword(kws)
        time.sleep(0.05)
    assert fi.search(q, index_path=store["index"]) is None


def test_a_lookup_reads_one_snapshot_while_the_index_is_rebuilt(store, monkeypatch):
    """grok first pass #1: a rebuild published mid-lookup must not pair the old vectors with the new ids."""
    monkeypatch.setenv(fi.FLAG, "1")
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    m = fi._state["matrix"]

    def rebuilding_embed(texts):
        if texts == ["the northwind invoice"]:
            os.remove(store["db"])
            _make_db(store["db"], [("f-zebra", "fact", "The zebra exhibit opens in March", "active")])
            fi.sync(store["db"], store["index"], embed=fake_embed)
            m.refresh()                                  # a concurrent full reload, published before we read
            assert m.snap.ids == ["f-zebra"]
        return fake_embed(texts)
    monkeypatch.setitem(fi._state, "embed", rebuilding_embed)
    fts, vec, _ = fi.search("the northwind invoice", index_path=store["index"])
    assert "f-bill" in vec and "f-zebra" not in vec + fts


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


# --- the three things the step-1 gate needed (0 of 230 labelled answers lost) ---------------------------------

def test_each_rankers_best_answer_is_served(store, monkeypatch):
    """Fusion alone ranks facts that sit mid-list in BOTH lists above a fact only one ranker has, even at its #1."""
    monkeypatch.setenv(fi.FLAG, "1")
    con = sqlite3.connect(store["db"])
    _add(con, [(f"x{i}", "fact", f"filler fact number {i}", "active") for i in range(6)])
    con.close()
    both = [f"x{i}" for i in range(6)]
    monkeypatch.setattr(fi, "search", lambda text, index_path=None, keywords=None: (["f-bill"] + both, both, 10 ** 9))
    got = _ids(fr.query_hybrid("anything at all", ["anything"], dbpath=store["db"], jsonpath=store["json"]))
    assert len(got) == fr.K and got[0] == "f-bill"


def test_a_short_question_keeps_its_rarest_three_words(store, monkeypatch):
    fi.sync(store["db"], store["index"], embed=fake_embed)
    monkeypatch.setattr(fi, "DF_MIN_CAP", 0)
    monkeypatch.setattr(fi, "DF_MAX_FRAC", 0.0)          # every word counts as common
    df = dict(sqlite3.connect(store["index"]).execute("SELECT term, n FROM df"))
    assert fi.fts_match("luna dog vet appointment", df, len(ROWS)).count('"') == 2 * fi.MIN_FTS_TERMS
    # a word no fact holds in that form is searched last (its stem may still match: "invoice" / "invoices")
    assert fi.fts_match("zzzunknownzzz luna", df, len(ROWS)) == '"luna" OR "zzzunknownzzz"'


def test_word_counts_follow_sync(store):
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    con = sqlite3.connect(store["db"])
    _add(con, [("f-luna2", "fact", "Luna chewed the sofa", "active")])
    con.close()
    fi.sync(store["db"], store["index"], embed=fake_embed)
    ix = dict(sqlite3.connect(store["index"]).execute("SELECT term, n FROM df"))
    m = fi._state["matrix"]
    assert m.refresh(max_inline=10) and ix["luna"] == m.snap.df["luna"] == 2
    assert "f-luna2" in fi.search("luna sofa", index_path=store["index"], keywords=["luna", "sofa"])[0]


def test_fts_list_prefers_the_fact_holding_more_of_the_question(store):
    con = sqlite3.connect(store["db"])
    _add(con, [("f-rep", "fact", "mango mango", "active"),                      # the one rare word, twice
               ("f-all", "fact", "we saw a zebra and ate an apple", "active")] +   # two common words
              [(f"z{i}", "fact", f"the zebra pen and the apple tree, note {i}", "active") for i in range(8)])
    con.close()
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    ix = fi._open_ro(store["index"])
    bm25 = [r[0] for r in ix.execute("SELECT doc.fact_id FROM fts JOIN doc ON doc.rowid = fts.rowid WHERE fts "
                                     "MATCH '\"zebra\" OR \"apple\" OR \"mango\"' ORDER BY bm25(fts)")]
    assert bm25.index("f-rep") < bm25.index("f-all")     # bm25 alone prefers the rare word repeated
    got = fi._snap_fts(fi._state["matrix"].snap, "zebra apple mango", 20, ["zebra", "apple", "mango"])
    assert got.index("f-all") < got.index("f-rep")


def test_lookups_check_for_new_rows_at_most_every_interval(store, monkeypatch):
    """The clock is pinned near zero, as on a freshly booted CI runner (the first CI run failed exactly there)."""
    monkeypatch.setattr(fi, "REFRESH_EVERY_S", 3600.0)
    monkeypatch.setattr(fi.time, "monotonic", lambda: 10.0)
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    fi.search("luna", index_path=store["index"])
    con = sqlite3.connect(store["db"])
    _add(con, [("f-boat", "fact", "The sailboat needs new sails before the regatta", "active")])
    con.close()
    fi.sync(store["db"], store["index"], embed=fake_embed)
    assert "f-boat" not in fi.search("sailboat regatta", index_path=store["index"])[0]
    fi._state["matrix"].checked = fi.NEVER               # as after a failed refresh
    fi.search("sailboat regatta", index_path=store["index"])     # hands the refresh to the loader
    for _ in range(200):
        if "f-boat" in fi.search("sailboat regatta", index_path=store["index"])[0]:
            return
        time.sleep(0.01)
    raise AssertionError("the background refresh never published the synced row")


def test_a_fact_ingested_since_the_last_sync_is_served(store, monkeypatch):
    """gating review #1: the index lags facts.db between ingest and sync (or for good if sync fails); keyword
    recall over the rows past the index keeps those facts in."""
    monkeypatch.setenv(fi.FLAG, "1")
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    con = sqlite3.connect(store["db"])
    _add(con, [("f-boat", "fact", "The sailboat needs new sails before the regatta", "active"),
               ("f-old2", "fact", "The sailboat regatta was moved to May", "superseded")])
    con.close()
    q = "sailboat regatta"
    kws = fr.extract_keywords(q)
    assert "f-boat" in _ids(fr._query_sources_keyword(kws))
    got = _ids(fr.query_sources(kws, text=q))
    assert "f-boat" in got and "f-old2" not in got


def test_an_index_far_behind_serves_keyword_recall(store, monkeypatch):
    monkeypatch.setenv(fi.FLAG, "1")
    monkeypatch.setattr(fr, "TAIL_LIMIT", 1)
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    con = sqlite3.connect(store["db"])
    _add(con, [("f-boat", "fact", "The sailboat needs new sails before the regatta", "active"),
               ("f-kite", "fact", "The kite festival is on the beach", "active")])
    con.close()
    q = "sailboat regatta"
    kws = fr.extract_keywords(q)
    assert fr.query_hybrid(q, kws) is None
    assert fr.query_sources(kws, text=q) == fr._query_sources_keyword(kws)


def test_a_lookup_never_refreshes_on_its_own_thread(store, monkeypatch):
    """gating review #2: appending synced rows copies the whole matrix (~0.5-1 s CPU on 50k rows)."""
    monkeypatch.setenv(fi.FLAG, "1")
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    seen = []
    real = fi._Matrix.refresh

    def spy(self, max_inline=None):
        seen.append(threading.current_thread().name)
        return real(self, max_inline)
    monkeypatch.setattr(fi._Matrix, "refresh", spy)
    con = sqlite3.connect(store["db"])
    _add(con, [("f-boat", "fact", "The sailboat needs new sails before the regatta", "active")])
    con.close()
    fi.sync(store["db"], store["index"], embed=fake_embed)
    for _ in range(200):
        fi.search("sailboat regatta", index_path=store["index"])
        if seen:
            break
        time.sleep(0.01)
    assert seen and "MainThread" not in seen


def test_facts_preamble_with_the_flag_on(store, monkeypatch):
    monkeypatch.setenv(fi.FLAG, "1")
    monkeypatch.setenv(fr.FLAG, "1")
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    block = fr.facts_preamble("did the mechanic fix my automobile", budget_ms=2000)
    assert "repaired now" in block and "quoted four hundred" not in block


def test_a_refresh_that_died_halfway_does_not_wedge_the_next(store, monkeypatch):
    """Its rows were already in the shared FTS copy; the next refresh re-inserts them."""
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    m = fi._state["matrix"]
    con = sqlite3.connect(store["db"])
    _add(con, [("f-boat", "fact", "The sailboat needs new sails before the regatta", "active")])
    con.close()
    fi.sync(store["db"], store["index"], embed=fake_embed)
    real = np.vstack                                     # dies after the rows went into the shared FTS copy
    monkeypatch.setattr(np, "vstack", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("died")))
    with pytest.raises(RuntimeError):
        m.refresh()
    monkeypatch.setattr(np, "vstack", real)
    assert m.refresh() and "f-boat" in m.snap.ids


def test_a_failed_load_is_not_retried_on_every_turn(store, monkeypatch):
    fi.sync(store["db"], store["index"], embed=fake_embed)
    tries = []

    def broken():
        tries.append(1)
        raise RuntimeError("no model")
    for _ in range(20):
        assert fi.search("luna", index_path=store["index"], embed_factory=broken) is None
        time.sleep(0.01)
    assert len(tries) == 1


def test_a_lookup_sees_a_row_that_replaced_the_top_one(store, monkeypatch):
    """The walked-back top row and its replacement share a doc rowid, so the row count alone looks unchanged;
    the generation tells the reader to reload."""
    monkeypatch.setenv(fi.FLAG, "1")
    fi.sync(store["db"], store["index"], embed=fake_embed)
    _ready(store["index"])
    fi.search("luna", index_path=store["index"])
    con = sqlite3.connect(store["db"])
    con.execute("DELETE FROM facts WHERE fact_id='f-dog'")
    con.commit()
    _add(con, [("f-zebra", "fact", "The zebra exhibit opens in March", "active")])
    con.close()
    assert fi.sync(store["db"], store["index"], embed=fake_embed)["removed"] == 1
    found = False
    for _ in range(200):
        got = fi.search("zebra exhibit march", index_path=store["index"])
        if got and "f-zebra" in got[0] + got[1]:
            found = True
            break
        time.sleep(0.01)
    assert found
