"""PARALLEL RECALL (gm msg_5dd2ce28, audit #1 (a)+(b)). Measured 2026-10-09: streaming saves 0 s;
the model is ~0.75 s of the p50 1.61 s; the rest is OUR serial pre-model work (build_context with
worldview, then semantic-recall <=300 ms, then facts-recall <=250 ms). Run the recalls CONCURRENTLY
with build_context under ONE shared deadline. gm's conditions, each tested here:
  1. a late recall is OMITTED, never awaited;
  2. with everything on time, the context is byte-identical to the serial path (same blocks, order);
  3. (graded live later: Request -> generate() START over >=50 real turns, + AUDIBLE-LATENCY);
  4. the real recall libraries are safe to run concurrently with each other: proven, not assumed.
"""
import concurrent.futures as cf
import importlib.util
import pathlib
import sqlite3
import time

import pytest

# public: the semantic_memory library is not shipped, and test_semantic_recall skips its whole module
# without it. Importing it HERE skipped conditions 1 and 2 as well, which need no library at all. Only
# the real-library test (condition 4) imports it, and only it skips when the library is absent.
from services.arturo import semantic_recall as sr
from services.arturo import facts_recall as fr
from services.arturo.test_facts_recall import _make_facts_db


# ---------------- condition 4: real libraries, concurrently ----------------

def _sem_db(tmp_path, fake_embed):
    from semantic_memory import db as smdb, index as smi
    dbp = str(tmp_path / "sm.db")
    smdb.init_db(dbp)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name, text in {"telemetry-notes.md": "telemetry deriver accuracy notes and the shadow gate",
                       "voice-restore.md": "voice proxy restore runbook for the arturo pipeline"}.items():
        (corpus / name).write_text(text * 3)
    smi.index_paths(dbp, [str(corpus / n) for n in ("telemetry-notes.md", "voice-restore.md")],
                    scope="test", embed_fn=fake_embed)
    return dbp


def test_real_recall_libraries_are_safe_concurrently(tmp_path, monkeypatch):
    try:
        from services.arturo.test_semantic_recall import fake_embed   # applies its path shim
    except BaseException as e:                                         # its module-level skip
        pytest.skip(f"semantic_memory library not available: {e}")
    monkeypatch.setenv(sr.FLAG, "1")
    monkeypatch.setenv(fr.FLAG, "1")
    sr._reset_for_tests()
    sdb = _sem_db(tmp_path, fake_embed)
    fdb = str(tmp_path / "facts.db")
    _make_facts_db(fdb)
    sq = "tell me about the voice proxy restore runbook"
    fq = "what's going on with the watch uplink for arturo"
    s0 = sr.recall_preamble(sq, dbpath=sdb, embed_fn=fake_embed, budget_ms=5000)
    f0 = fr.facts_preamble(fq, dbpath=fdb, budget_ms=5000)
    assert s0 and f0, "both recalls must produce a block serially (else the test proves nothing)"
    n = 30
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        for _ in range(n):
            a = ex.submit(sr.recall_preamble, sq, dbpath=sdb, embed_fn=fake_embed, budget_ms=5000)
            b = ex.submit(fr.facts_preamble, fq, dbpath=fdb, budget_ms=5000)
            assert a.result(10) == s0
            assert b.result(10) == f0
    con = sqlite3.connect(sdb)
    try:
        logged = con.execute("SELECT COUNT(*) FROM queries_log").fetchone()[0]
    finally:
        con.close()
    assert logged == n + 1, f"semantic queries_log lost writes under concurrency: {logged} != {n + 1}"
    sr._reset_for_tests()


# ---------------- conditions 1 + 2 at the CLM seam ----------------

def _proxy(monkeypatch, tmp_path, sem, facts, ctx_delay=0.0):
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(tmp_path / "vc"))
    monkeypatch.setenv("ARTURO_SEMANTIC_RECALL", "1")
    monkeypatch.setenv("ARTURO_FACTS_RECALL", "1")
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_pr", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod._REQ_GUARD, "is_duplicate", lambda *a, **k: False)

    def _ctx(calling_channel=None):
        time.sleep(ctx_delay)
        return "CTX"
    monkeypatch.setattr(mod, "build_context", _ctx)
    monkeypatch.setattr(sr, "recall_preamble", sem)
    monkeypatch.setattr(fr, "facts_preamble", facts)
    seen = []

    class _Msg:
        tool_calls = None
        content = "ok"

    class _Choice:
        finish_reason = "stop"
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _C:
        def create(self, **kw):
            seen.append((time.time(), kw["messages"][0]["content"]))
            return _Resp()

    # public: the brain seam (services/arturo/brain.py) replaced the bare openai client
    _cmp = _C()
    monkeypatch.setattr(mod.brain, "complete", lambda **kw: _cmp.create(**kw))
    monkeypatch.setattr(mod, "BEARER_TOKEN", "test-bearer")
    return mod, seen


def _ask(mod):
    c = mod.app.test_client()
    t0 = time.time()
    c.post("/v1/chat/completions",
           json={"messages": [{"role": "user", "content": "what is the voice proxy restore runbook"}]},
           headers={"Authorization": f"Bearer {mod.BEARER_TOKEN}"},
           environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data()
    return t0


def test_context_identical_serial_vs_parallel(monkeypatch, tmp_path):
    out = {}
    for flag in ("0", "1"):
        monkeypatch.setenv("ARTURO_PARALLEL_RECALL", flag)
        mod, seen = _proxy(monkeypatch, tmp_path, lambda t, **k: "RECALL-BLOCK",
                           lambda t, **k: "FACTS-BLOCK")
        _ask(mod)
        out[flag] = seen[0][1]
    assert out["0"] == out["1"], "parallel must not change the context's content or order"
    assert out["1"].index("RECALL-BLOCK") < out["1"].index("FACTS-BLOCK")


def test_late_recall_is_omitted_not_awaited(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTURO_PARALLEL_RECALL", "1")

    def slow_sem(t, **k):
        time.sleep(1.5)
        return "RECALL-BLOCK"
    mod, seen = _proxy(monkeypatch, tmp_path, slow_sem, lambda t, **k: "FACTS-BLOCK")
    t0 = _ask(mod)
    pre_model = seen[0][0] - t0
    assert "RECALL-BLOCK" not in seen[0][1] and "FACTS-BLOCK" in seen[0][1]
    assert pre_model < 1.0, f"a late recall was awaited: {pre_model:.2f}s before the model call"


def test_parallel_costs_the_slowest_not_the_sum(monkeypatch, tmp_path):
    def sem(t, **k):
        time.sleep(0.25)
        return "RECALL-BLOCK"

    def facts(t, **k):
        time.sleep(0.25)
        return "FACTS-BLOCK"
    pre = {}
    for flag in ("0", "1"):
        monkeypatch.setenv("ARTURO_PARALLEL_RECALL", flag)
        mod, seen = _proxy(monkeypatch, tmp_path, sem, facts, ctx_delay=0.2)
        t0 = _ask(mod)
        pre[flag] = seen[0][0] - t0
        assert "RECALL-BLOCK" in seen[0][1] and "FACTS-BLOCK" in seen[0][1]
    assert pre["0"] >= 0.65, pre                    # serial: 0.2 + 0.25 + 0.25
    assert pre["1"] <= pre["0"] - 0.3, pre          # parallel: ~max(0.2, 0.25)
