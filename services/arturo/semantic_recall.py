"""Arturo in-call semantic recall — flag-gated preamble wiring (DEC-1788771883922080).

Consumes the standalone semantic_memory library (scripts/semantic_memory, landed additively
by gm's SAFE-PREP — NEVER a merge of the diverged branch) to append a <=250-token
paths-only RECALL block to the per-turn system context in arturo-proxy.py.

Posture (congruence-settled):
  - INERT unless ARTURO_SEMANTIC_RECALL=1 — the proxy checks the env var BEFORE importing
    this module, and this module never imports semantic_memory/fastembed unless a recall
    actually runs. Flag off = byte-identical proxy behavior.
  - Lazy IN-PROCESS embedder (measured +225MB RSS, not the ~700MB early estimate): the
    model loads in a background daemon thread kicked by the first enabled voice turn —
    never on the turn path, never at proxy boot. onnxruntime is capped to 1 intra-op
    thread (OMP_NUM_THREADS, set only if unset, before first fastembed import).
  - HARD BUDGET WALL (ARTURO_SEMANTIC_RECALL_BUDGET_MS, default 300): embed+query run in
    a worker thread; on timeout the turn proceeds with no preamble. db_connect's
    busy_timeout=30000 therefore can never stall a live voice turn.
  - SINGLE-FLIGHT: at most one recall worker in flight (reviewer note — an abandoned
    worker may hold a connection up to busy_timeout; overlapping turns skip recall).
  - queries_log rides WAL + per-execution PK (library-owned); a once-per-boot 14-day TTL
    prune runs in the loader thread, off the turn path.
  - NEVER touches MEMORY.md — this is a query-side consumer only.
"""
import logging
import os
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("arturo-semantic-recall")

FLAG = "ARTURO_SEMANTIC_RECALL"
BUDGET_ENV = "ARTURO_SEMANTIC_RECALL_BUDGET_MS"
DEFAULT_BUDGET_MS = 300
MAX_TOKENS = 250
MAX_CHARS = MAX_TOKENS * 4          # ~4 chars/token estimator, hard char clamp
K = 4
MIN_QUERY_CHARS = 8
TTL_DAYS = 14
SNIPPET_CHARS = 140

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", Path.home() / "scripts/agent-orchestra"))

_HEADER = (
    "=== RECALL (semantic memory — past docs relevant to what the operator just said) ===\n"
    "These are POINTERS from your long-term memory. Use read_file on a path only if\n"
    "the operator's question needs the detail; otherwise answer from the snippet."
)

# module state (single proxy process)
_inflight = threading.BoundedSemaphore(1)
_loader_lock = threading.Lock()
_loader_started = False
_model_ready = threading.Event()
_warned = set()


def _warn_once(key, msg):
    if key not in _warned:
        _warned.add(key)
        log.warning(msg)


def _reset_for_tests():
    global _loader_started, _inflight
    _loader_started = False
    _model_ready.clear()
    _warned.clear()
    _inflight = threading.BoundedSemaphore(1)


def enabled():
    return os.environ.get(FLAG, "") == "1"


def default_db_path():
    return str(ORCHESTRA_DIR / "state" / "semantic-memory.db")


def latest_user_text(messages):
    """Latest non-empty user turn from an OpenAI-style message list ('' if none)."""
    for m in reversed(messages or []):
        if m.get("role") == "user" and (m.get("content") or "").strip():
            return m["content"].strip()
    return ""


def _import_sm():
    """Import the semantic_memory library from <orchestra>/scripts (SAFE-PREP landing spot).
    Called only on an enabled, non-trivial recall — flag off never reaches this."""
    scripts = str(ORCHESTRA_DIR / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import semantic_memory.query as smq  # noqa: PLC0415
    return smq


def prune_queries_log(dbpath, days=TTL_DAYS):
    """Bounded queries_log growth without a new cron: DELETE rows older than `days`.
    Runs once per proxy boot in the loader thread; the sweep cron may own a real TTL later."""
    try:
        import semantic_memory.db as smdb  # noqa: PLC0415
        con = smdb.connect(dbpath)
        try:
            cur = con.execute("DELETE FROM queries_log WHERE ts < ?",
                              [time.time() - days * 86400])
            con.commit()
            if cur.rowcount:
                log.info(f"queries_log TTL prune: {cur.rowcount} rows older than {days}d")
        finally:
            con.close()
    except Exception as e:
        log.error(f"queries_log prune failed (non-fatal): {e}")


def _loader(dbpath):
    """Background model warm-up + once-per-boot TTL prune. Never on the turn path."""
    try:
        os.environ.setdefault("OMP_NUM_THREADS", "1")   # cap onnxruntime intra-op threads
        scripts = str(ORCHESTRA_DIR / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from semantic_memory import embed as sme  # noqa: PLC0415
        sme.embed_query("warmup: arturo semantic recall loader")
        prune_queries_log(dbpath)
        _model_ready.set()
        log.info("semantic-recall embedder READY (lazy in-process load complete)")
    except Exception as e:
        log.error(f"semantic-recall loader failed — recall stays disabled this boot: {e}")


def _ensure_loader(dbpath):
    global _loader_started
    with _loader_lock:
        if _loader_started:
            return
        _loader_started = True
    threading.Thread(target=_loader, args=(dbpath,), daemon=True,
                     name="semantic-recall-loader").start()


def _render(results):
    """Paths + 1-line snippets under the hard char cap. Never content blobs."""
    lines = [_HEADER]
    used = len(_HEADER)
    for r in results[:K]:
        snip = " ".join((r.get("snippet") or "").split())[:SNIPPET_CHARS]
        line = f"- {r['path']} — {snip}"
        if used + 1 + len(line) > MAX_CHARS:
            room = MAX_CHARS - used - 1
            if room > len(r["path"]) + 4:   # only add a truncated line if the path still fits
                lines.append(line[:room])
            break
        lines.append(line)
        used += 1 + len(line)
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def recall_preamble(user_text, dbpath=None, embed_fn=None, budget_ms=None):
    """Return a <=250-token RECALL block for this turn, or '' (recall is strictly additive —
    every failure/skip path degrades to no-preamble, never an error into the turn)."""
    if not enabled():
        return ""
    text = (user_text or "").strip()
    if len(text) < MIN_QUERY_CHARS or text in ("...", "."):
        return ""
    dbpath = dbpath or default_db_path()
    if not os.path.exists(dbpath):
        _warn_once("nodb", f"semantic-recall: db missing ({dbpath}) — recall inert until SAFE-PREP lands the index")
        return ""
    try:
        smq = _import_sm()
    except Exception as e:
        _warn_once("nolib", f"semantic-recall: semantic_memory library not importable — recall inert ({e})")
        return ""
    # embed_fn injected (tests) bypasses the model loader; production needs the warm model.
    if embed_fn is None:
        _ensure_loader(dbpath)
        if not _model_ready.is_set():
            return ""
    if budget_ms is None:
        try:
            budget_ms = int(os.environ.get(BUDGET_ENV, DEFAULT_BUDGET_MS))
        except ValueError:
            budget_ms = DEFAULT_BUDGET_MS

    sem = _inflight                             # bind the instance: a test reset must not swap it mid-flight
    if not sem.acquire(blocking=False):         # single-flight: overlapping turn skips
        return ""
    result = {}

    def _work():
        try:
            result["r"] = smq.query(dbpath, text, scope=None, k=K,
                                    agent="arturo-voice", embed_fn=embed_fn)
        except Exception as e:
            result["e"] = e
        finally:
            sem.release()                       # released by the WORKER — in-flight means in-flight

    t = threading.Thread(target=_work, daemon=True, name="semantic-recall-query")
    t.start()
    t.join(budget_ms / 1000.0)
    if "e" in result:
        log.error(f"semantic-recall query error (non-fatal): {result['e']}")
        return ""
    if "r" not in result:
        log.info(f"semantic-recall: budget {budget_ms}ms exceeded — turn proceeds without recall")
        return ""
    if not result["r"]:
        return ""
    try:
        return _render(result["r"])
    except Exception as e:
        log.error(f"semantic-recall render error (non-fatal): {e}")
        return ""
