"""RED-first tests — Arturo in-call semantic recall wiring (DEC-1788771883922080).

The semantic_memory library lands in the live tree via gm's SAFE-PREP (additive checkout).
Until then these tests import it from the semantic-memory-mvp worktree — the REAL library,
not a stub — via the path shim below. The shim is a no-op once scripts/semantic_memory exists.
"""
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

ORCHESTRA = Path(__file__).resolve().parents[2]
_WORKTREE_SCRIPTS = Path.home() / ".config/superpowers/worktrees/agent-orchestra/semantic-memory-mvp/scripts"
if not (ORCHESTRA / "scripts" / "semantic_memory").exists():
    if not _WORKTREE_SCRIPTS.joinpath("semantic_memory").exists():
        pytest.skip("semantic_memory library not available (live tree or worktree)", allow_module_level=True)
    sys.path.insert(0, str(_WORKTREE_SCRIPTS))
else:
    sys.path.insert(0, str(ORCHESTRA / "scripts"))

from services.arturo import semantic_recall as sr  # noqa: E402


# --- deterministic fake embedder: maps known keywords to fixed orthogonal-ish vectors ---
DIM = 384

def _vec(seed):
    import numpy as np
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(DIM).astype("float32")
    return v / np.linalg.norm(v)

_TOPIC_SEEDS = {"telemetry": 1, "rotation": 2, "voice": 3}

def fake_embed(texts):
    out = []
    for t in texts:
        for word, seed in _TOPIC_SEEDS.items():
            if word in t.lower():
                out.append(_vec(seed))
                break
        else:
            out.append(_vec(99))
    return out


@pytest.fixture()
def fixture_db(tmp_path):
    """A real semantic-memory db indexed with the real library + the fake embedder."""
    from semantic_memory import db as smdb, index as smi
    dbp = str(tmp_path / "sm.db")
    smdb.init_db(dbp)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    docs = {
        "telemetry-notes.md": "telemetry deriver accuracy notes and the shadow gate design",
        "rotation-protocol.md": "rotation handoff protocol v2 and the promote successor flow",
        "voice-restore.md": "voice proxy restore runbook for the arturo pipeline",
    }
    for name, text in docs.items():
        (corpus / name).write_text(text * 3)
    smi.index_paths(dbp, [str(corpus / n) for n in docs], scope="test", embed_fn=fake_embed)
    return dbp


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    sr._reset_for_tests()
    monkeypatch.setenv(sr.FLAG, "1")
    yield
    sr._reset_for_tests()


# ---------- 1. preamble injected on a turn ----------

def test_preamble_injected_with_relevant_path(fixture_db):
    p = sr.recall_preamble("what was the telemetry accuracy issue",
                           dbpath=fixture_db, embed_fn=fake_embed, budget_ms=5000)
    assert p, "expected a non-empty recall preamble"
    assert "RECALL" in p
    assert "telemetry-notes.md" in p
    # paths-only doctrine: the preamble carries pointers + snippets, never big content dumps
    assert len(p) <= sr.MAX_CHARS


def test_seam_appends_to_context(fixture_db):
    """The proxy seam contract: context + preamble, driven off the latest user text."""
    messages = [{"role": "assistant", "content": "hi"},
                {"role": "user", "content": "tell me about the rotation handoff"}]
    txt = sr.latest_user_text(messages)
    assert txt == "tell me about the rotation handoff"
    p = sr.recall_preamble(txt, dbpath=fixture_db, embed_fn=fake_embed, budget_ms=5000)
    assert "rotation-protocol.md" in p


# ---------- 2. token cap honored ----------

def test_token_cap_adversarial(tmp_path):
    from semantic_memory import db as smdb, index as smi
    dbp = str(tmp_path / "sm.db")
    smdb.init_db(dbp)
    corpus = tmp_path / ("deep/" + "sub" * 40)
    corpus.mkdir(parents=True)
    for i in range(12):
        f = corpus / (f"voice-very-long-document-name-{i:02d}-" + "x" * 120 + ".md")
        f.write_text("voice proxy " + "long snippet content words " * 60)
    smi.index_paths(dbp, [str(f) for f in corpus.iterdir()], scope="test", embed_fn=fake_embed)
    p = sr.recall_preamble("voice question", dbpath=dbp, embed_fn=fake_embed, budget_ms=5000)
    assert p
    assert len(p) <= sr.MAX_CHARS, f"preamble {len(p)} chars exceeds cap {sr.MAX_CHARS}"
    assert sr.MAX_CHARS <= 250 * 4  # the cap itself honors <=250 tokens at ~4 chars/token


# ---------- 3. queries_log safe under concurrent turns ----------

def test_queries_log_concurrent_writes(fixture_db):
    from semantic_memory import query as smq
    errs, n = [], 16
    def one(i):
        try:
            smq.query(fixture_db, f"telemetry q{i}", scope=None, k=2,
                      agent="arturo-voice", embed_fn=fake_embed)
        except Exception as e:  # pragma: no cover
            errs.append(e)
    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errs
    con = sqlite3.connect(fixture_db)
    rows = con.execute("SELECT COUNT(*) FROM queries_log").fetchone()[0]
    con.close()
    assert rows >= n  # every concurrent execution landed its row


def test_single_flight_skips_overlapping_recall(fixture_db):
    """At most ONE recall worker in flight — overlapping calls degrade to ''. Guards the
    30s busy_timeout pileup risk flagged in review."""
    release = threading.Event()
    def slow_embed(texts):
        release.wait(5)
        return fake_embed(texts)
    results = {}
    def first():
        results["a"] = sr.recall_preamble("telemetry one", dbpath=fixture_db,
                                          embed_fn=slow_embed, budget_ms=4000)
    t = threading.Thread(target=first)
    t.start()
    time.sleep(0.2)  # first call is now in flight, blocked in slow_embed
    results["b"] = sr.recall_preamble("telemetry two", dbpath=fixture_db,
                                      embed_fn=fake_embed, budget_ms=4000)
    release.set()
    t.join()
    assert results["b"] == ""      # overlapping call skipped
    assert "telemetry-notes.md" in results["a"]


# ---------- 4. INERT behind the flag ----------

def test_flag_off_returns_empty_and_never_imports(monkeypatch, fixture_db):
    monkeypatch.delenv(sr.FLAG, raising=False)
    sr._reset_for_tests()
    saved = {k: sys.modules.pop(k) for k in list(sys.modules) if k.split(".")[0] == "semantic_memory"}
    try:
        assert not sr.enabled()
        assert sr.recall_preamble("what about the telemetry", dbpath=fixture_db) == ""
        assert not any(k.split(".")[0] == "semantic_memory" for k in sys.modules), \
            "flag OFF must not import semantic_memory"
    finally:
        sys.modules.update(saved)


def test_proxy_seam_is_env_gated_and_default_voice_channel_covered():
    """The seam in arturo-proxy.py must gate on the env var BEFORE importing this module,
    and metadata-absent requests default to channel 'voice' (reviewer note #2) — so the
    flag, not the channel, is what keeps local curls inert."""
    src = (ORCHESTRA / "services/arturo/arturo-proxy.py").read_text()
    assert "ARTURO_SEMANTIC_RECALL" in src, "proxy seam missing"
    seam = src[src.index("ARTURO_SEMANTIC_RECALL"):]
    assert "semantic_recall" in seam[:600], "flag check must guard the semantic_recall import"


# ---------- 5. degrade paths ----------

def test_missing_db_degrades_empty(tmp_path):
    p = sr.recall_preamble("telemetry question",
                           dbpath=str(tmp_path / "nope.db"), embed_fn=fake_embed)
    assert p == ""


def test_budget_timeout_degrades_empty(fixture_db):
    def stuck_embed(texts):
        time.sleep(2)
        return fake_embed(texts)
    t0 = time.time()
    p = sr.recall_preamble("telemetry question", dbpath=fixture_db,
                           embed_fn=stuck_embed, budget_ms=150)
    assert p == ""
    assert time.time() - t0 < 1.5, "budget wall must return well before the worker finishes"


def test_query_error_degrades_empty(fixture_db):
    def boom(texts):
        raise RuntimeError("embedder exploded")
    assert sr.recall_preamble("telemetry question", dbpath=fixture_db, embed_fn=boom,
                              budget_ms=2000) == ""


# ---------- 6. trivial/silent turns never query ----------

@pytest.mark.parametrize("text", ["", "...", ".", "ok", "   "])
def test_trivial_text_no_query_no_log(text, fixture_db):
    assert sr.recall_preamble(text, dbpath=fixture_db, embed_fn=fake_embed) == ""
    con = sqlite3.connect(fixture_db)
    rows = con.execute("SELECT COUNT(*) FROM queries_log").fetchone()[0]
    con.close()
    assert rows == 0


def test_latest_user_text_extraction():
    msgs = [{"role": "user", "content": "first"},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "the real question"},
            {"role": "assistant", "content": ""}]
    assert sr.latest_user_text(msgs) == "the real question"
    assert sr.latest_user_text([]) == ""
    assert sr.latest_user_text([{"role": "assistant", "content": "x"}]) == ""


# ---------- 7. TTL prune ----------

def test_ttl_prune_bounds_queries_log(fixture_db):
    con = sqlite3.connect(fixture_db)
    now = time.time()
    con.execute("INSERT INTO queries_log(query_hash,query_text,ts) VALUES ('q_old','old', ?)",
                [now - 20 * 86400])
    con.execute("INSERT INTO queries_log(query_hash,query_text,ts) VALUES ('q_new','new', ?)",
                [now - 1 * 86400])
    con.commit(); con.close()
    sr.prune_queries_log(fixture_db, days=14)
    con = sqlite3.connect(fixture_db)
    left = [r[0] for r in con.execute("SELECT query_hash FROM queries_log")]
    con.close()
    assert "q_old" not in left and "q_new" in left
