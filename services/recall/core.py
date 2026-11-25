"""Fleet semantic-recall core — the ONE library seam behind the recall service and the CLI fallback.

The chosen design (2026-09-21): every runtime (claude / gemini / codex) gets the
same three bounded, cited blocks Arturo gets in a voice turn — FACTS (facts.db keyword), WORLDVIEW
(L2 beliefs + fluid synthesis, L1 citation), RECALL (vector over docs) — by importing Arturo's
facts_recall / semantic_recall / semantic_memory as LIBRARIES. Nothing here touches Arturo's read path.

Binding peer conditions (all five live in this module):
  1. Bounded worker POOL (default N=4), queue-wait INSIDE the caller's budget, and an EXPLICIT
     degraded flag + reason on every answer. Silence is never the degrade signal.
  2. The library's query_hash rides every answer so `ack(query_hash, path, signal)` produces
     feedback/scores rows (anti-spoof + dedupe + clamp are the library's own).
  3. queries_log prune is a DAILY tick (prune_tick), never once-per-boot.
  4. facts_recall._is_noise + the worldview snippet guard run on ALL blocks, including the
     vector-off fallback path the CLI uses when the service is down.
  5. facts.db is opened read-only by the library (mode=ro); the only writes are the library's own
     queries_log / feedback / scores rows in semantic-memory.db.
Safety contract copied from Arturo: hard budget wall per block in a worker thread; every failure
degrades to an empty block with a named reason, never an exception to the caller.
"""
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger("recall-core")

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", Path.home() / "scripts/agent-orchestra"))
_ROOT = str(Path(__file__).resolve().parents[2])
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.arturo import facts_recall as fr       # noqa: E402
from services.arturo import semantic_recall as sr    # noqa: E402

DEFAULT_POOL_N = 4
DEFAULT_BUDGETS_MS = {"facts": 250, "worldview": 250, "recall": 300}   # inherited from Arturo
DEFAULT_K = 5
MIN_QUERY_CHARS = fr.MIN_QUERY_CHARS
PRUNE_TTL_DAYS = sr.TTL_DAYS
PRUNE_INTERVAL_S = 86400
BLOCKS = ("facts", "worldview", "recall")

# "where is Shaw", "where am I", "Shaw's location / time zone": the questions the shaw.location slot answers.
_LOCATION_Q = re.compile(
    r"\bwhere(?:'s|\s+is|\s+am|\s+are|\s+does|\s+did)\s+(?:shaw|he|i|you)\b"
    r"|\bshaw(?:'s|\s+cole's)?\s+(?:location|whereabouts|time\s*zone|timezone)\b"
    r"|\bwhere\s+(?:shaw|he)\s+(?:is|lives|lives now|is now)\b", re.I)


def open_slot_facts(facts_db, slot_key):
    """The open (valid_to NULL) facts of a single-valued slot, the location file's own fact first, newest first.
    Read-only; [] on a db without the slot columns (pre-slots) or any read error (recall never fails on it)."""
    import sqlite3  # noqa: PLC0415
    try:
        con = sqlite3.connect(f"file:{facts_db}?mode=ro", uri=True, timeout=1.0)
        try:
            rows = con.execute(
                "SELECT fact_id, ts, kind, text, expiry_class FROM facts WHERE slot_key=? AND valid_to IS NULL "
                f"AND {fr.SERVED_SQL} "
                "ORDER BY (json_extract(provenance,'$.source_kind')='shaw-location') DESC, valid_from DESC LIMIT 2",
                (slot_key,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    return [{"fact_id": r[0], "ts": r[1], "kind": r[2], "text": r[3], "expiry_class": r[4]} for r in rows]


def closed_slot_ids(facts_db, slot_key, fact_ids):
    """Which of fact_ids are CLOSED members of the slot (valid_to set). set() on a pre-slots db or a read error."""
    import sqlite3  # noqa: PLC0415
    if not fact_ids:
        return set()
    try:
        con = sqlite3.connect(f"file:{facts_db}?mode=ro", uri=True, timeout=1.0)
        try:
            q = ",".join("?" * len(fact_ids))
            rows = con.execute(f"SELECT fact_id FROM facts WHERE slot_key=? AND valid_to IS NOT NULL "
                               f"AND fact_id IN ({q})", (slot_key, *fact_ids)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return set()
    return {r[0] for r in rows}
EMBED_STATES = ("unstarted", "loading", "ready", "failed", "off")
EMB_RETRY_S = float(os.environ.get("RECALL_EMB_RETRY_S", "300"))   # a failed load is retried at most this often
CALLS_SCHEMA = """CREATE TABLE IF NOT EXISTS recall_calls (
  id INTEGER PRIMARY KEY, query_hash TEXT, agent TEXT, ts REAL NOT NULL, degraded INTEGER NOT NULL,
  reason TEXT NOT NULL DEFAULT '', ms_facts REAL, ms_worldview REAL, ms_recall REAL,
  budget_facts INTEGER, budget_worldview INTEGER, budget_recall INTEGER, source TEXT, question_len INTEGER,
  vector INTEGER NOT NULL DEFAULT 1)"""
CALLS_MIGRATIONS = ("ALTER TABLE recall_calls ADD COLUMN vector INTEGER NOT NULL DEFAULT 1",)
DEFAULT_SWEEP_UTC_HOUR = 9           # 04:00 Tulum (UTC-5, no DST): the dream/ingest window (ingest cron 04:15 ET)
SWEEP_NICE = 10
SWEEP_CHILD_ARGV = [sys.executable, str(Path(__file__).resolve().parent / "sweep_child.py")]   # patchable in tests
SWEEP_CHILD_TIMEOUT_S = 3600
DEFAULT_SWEEP_LOAD_MAX = float(os.cpu_count() or 4)   # core count (8 here), same rule as the brain-voice watchdog; a fixed 4 deferred every tick at load 5-8


def _load_1m():
    try:
        return float(os.getloadavg()[0])
    except Exception:  # noqa: BLE001
        return 0.0
STATS_THRESHOLD_PCT = 5.0            # review R1: budget-exceeded above 5 percent of real calls => build 1b
DEFAULT_EXCLUDE_PREFIXES = ("burst", "test", "gemini-test", "codex-test", "devex-review", "seed:")


def default_facts_db():
    return os.environ.get("RECALL_FACTS_DB") or fr.default_db_path()


def default_sm_db():
    return os.environ.get("RECALL_SM_DB") or sr.default_db_path()


def _is_noise(text):
    try:
        return fr._is_noise(text or "")
    except Exception:            # noqa: BLE001 — a filter failure must never leak junk as a crash
        return False


def _empty_blocks():
    return {b: [] for b in BLOCKS}


def render(blocks):
    """Arturo's three renderers, joined. Empty blocks render nothing."""
    parts = []
    try:
        if blocks.get("recall"):
            parts.append(sr._render(blocks["recall"]))
        if blocks.get("facts"):
            parts.append(fr._render(blocks["facts"]))
        if blocks.get("worldview"):
            parts.append(fr._render_worldview(blocks["worldview"]))
    except Exception as e:       # noqa: BLE001
        log.error(f"render error (non-fatal): {e}")
    return "\n\n".join(p for p in parts if p)


class Recall:
    def __init__(self, facts_db=None, sm_db=None, pool_n=DEFAULT_POOL_N, budgets=None,
                 embed_fn=None, loader=None, prune_ttl_days=PRUNE_TTL_DAYS, k=DEFAULT_K,
                 embedder=True, sweep_enabled=None, sweep_utc_hour=None, sweep_resolve=None,
                 sweep_scope_for=None, sweep_load_max=None, source="service"):
        self.facts_db = facts_db or default_facts_db()
        self.sm_db = sm_db or default_sm_db()
        self.pool_n = int(pool_n)
        self.budgets = dict(DEFAULT_BUDGETS_MS, **(budgets or {}))
        self.embed_fn = embed_fn
        self._loader = loader
        self.prune_ttl_days = prune_ttl_days
        self.k = k
        self._slots = threading.BoundedSemaphore(self.pool_n)
        self._lock = threading.Lock()
        self._inflight = 0
        self._queued = 0
        self._emb_state = "unstarted" if embedder else "off"
        self._emb_ready = threading.Event()
        self._emb_lock = threading.Lock()
        self.emb_retry_s = EMB_RETRY_S
        self._emb_attempts = 0
        self._emb_last_attempt = None
        self._emb_last_error = None
        self._last_prune = None
        self.prunes = 0
        self.queries = 0
        self.degraded_count = 0
        self.warms = 0
        self.last_warm_ms = 0.0
        self._log_threads = []
        self._calls_table_ready = False
        # nightly index sweep (step 5 / condition 5): inside this process, niced, dream window
        self.sweep_enabled = (os.environ.get("RECALL_SWEEP", "1") != "0") if sweep_enabled is None else bool(sweep_enabled)
        self.sweep_utc_hour = int(os.environ.get("RECALL_SWEEP_UTC_HOUR", DEFAULT_SWEEP_UTC_HOUR)) if sweep_utc_hour is None else int(sweep_utc_hour)
        self._sweep_resolve = sweep_resolve
        self._sweep_scope_for = sweep_scope_for
        self.source = source                      # which PROCESS serves: "service" | "cli" (in-process fallback)
        self.sweep_load_max = float(os.environ.get("RECALL_SWEEP_LOAD_MAX", DEFAULT_SWEEP_LOAD_MAX)) if sweep_load_max is None else float(sweep_load_max)
        self._sweep_last_attempt = None
        self.sweep_deferred = 0
        self.sweep_skips = {}
        self.last_skip = None
        self.sweeps = 0
        self.last_sweep = None
        self.last_sweep_ts = 0.0
        self.started = time.time()

    # ------------------------------------------------------------ embedder (once, off the path)
    def _default_loader(self):
        scripts = str(ORCHESTRA_DIR / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from semantic_memory import embed as sme  # noqa: PLC0415
        sme.embed_query("warmup: fleet recall service loader")

    def _run_loader(self):
        try:
            os.environ.setdefault("OMP_NUM_THREADS", "1")   # cap onnxruntime intra-op threads
            (self._loader or self._default_loader)()
            self._emb_state = "ready"
            self._emb_last_error = None
            log.info("recall embedder READY (loaded once, background thread)")
        except Exception as e:   # noqa: BLE001
            self._emb_state = "failed"
            self._emb_last_error = str(e)[:300]
            log.error(f"recall embedder loader FAILED (attempt {self._emb_attempts}; retried after "
                      f"{self.emb_retry_s:.0f} s by the next query or sweep tick): {e}")
        finally:
            self._emb_ready.set()

    def ensure_embedder(self):
        """Start the loader once; after a failure, start it again at most once per emb_retry_s. A failure used
        to be final for the life of the process (09-29: cache wiped from /tmp, model back 10-05, RECALL off 10 d)."""
        now = time.time()
        with self._emb_lock:
            if self._emb_state == "failed":
                if self._emb_last_attempt is not None and now - self._emb_last_attempt < self.emb_retry_s:
                    return
            elif self._emb_state != "unstarted":
                return
            self._emb_state = "loading"
            self._emb_attempts += 1
            self._emb_last_attempt = now
            self._emb_ready.clear()
        threading.Thread(target=self._run_loader, daemon=True, name="recall-embedder-loader").start()

    def wait_embedder(self, timeout=None):
        if self._emb_state in ("off", "unstarted"):
            return False
        self._emb_ready.wait(timeout)
        return self._emb_state == "ready"

    # ------------------------------------------------------------ block queries (patchable seams)
    def _query_facts(self, keywords, k, text=""):
        facts = [f for f in fr.query_facts(self.facts_db, keywords, k=k) if not _is_noise(f.get("text"))]
        if text and _LOCATION_Q.search(text):
            # memory slice 1 step 2: a location question gets the CURRENT shaw.location slot fact first. Keyword
            # scoring alone ranks old durable "Shaw decision" rows above it ("shaw" matches nearly every fact).
            pinned = open_slot_facts(self.facts_db, "shaw.location")
            ids = {p["fact_id"] for p in pinned}
            closed = closed_slot_ids(self.facts_db, "shaw.location",
                                     [f.get("fact_id") for f in facts if f.get("fact_id")])
            # a CLOSED location fact (Florida) is history here; it may still be active for its other content
            facts = (pinned + [f for f in facts if f.get("fact_id") not in ids | closed])[:k]
        return facts

    def _query_worldview(self, keywords, k):
        return [e for e in fr.query_worldview(self.facts_db, keywords, k=k) if not _is_noise(e.get("text"))]

    def _query_recall(self, text, scope, k, agent):
        smq = sr._import_sm()
        res = smq.query(self.sm_db, text, scope=scope, k=k, agent=agent, embed_fn=self.embed_fn)
        kept = [r for r in res if not _is_noise(r.get("snippet"))]
        return kept, res.query_hash

    def _log_query(self, text, scope, agent):
        """queries_log row for a query whose vector path did not run (loading/off/vector-off), so the
        fleet still shows up in queries_log.agent and the answer still carries an ack-able hash.
        The INSERT runs in a background thread: the library's busy_timeout (30 s) must never sit on
        the request path (measured 1.3 s walls under a 12-call burst before this)."""
        # same per-execution id formula as semantic_memory.query._execution_id, minted here so the
        # keyword-only path (CLI fallback) never imports numpy/the vector module just for an id.
        raw = f"{text}|{scope}|{agent}|{time.time()}|{uuid.uuid4().hex}"
        qh = "q_" + hashlib.sha256(raw.encode()).hexdigest()[:24]

        def _write():
            try:
                sr._import_sm.__globals__  # keep the sys.path seam identical to the vector path
                scripts = str(ORCHESTRA_DIR / "scripts")
                if scripts not in sys.path:
                    sys.path.insert(0, scripts)
                import semantic_memory.db as smdb  # noqa: PLC0415  (db_connect only, no numpy)
                con = smdb.connect(self.sm_db)
                try:
                    con.execute("INSERT INTO queries_log(query_hash,query_text,scope,agent,ts,returned_doc_ids)"
                                " VALUES (?,?,?,?,?,?)", [qh, text, scope, agent, time.time(), json.dumps([])])
                    con.commit()
                finally:
                    con.close()
            except Exception as e:   # noqa: BLE001
                log.error(f"queries_log write failed (non-fatal): {e}")
        t = threading.Thread(target=_write, daemon=True, name="recall-qlog")
        with self._lock:
            self._log_threads = [x for x in self._log_threads if x.is_alive()] + [t]
        t.start()
        return qh

    def flush_logs(self, timeout=2.0):
        """Wait for pending queries_log writers (a short-lived CLI process must not exit before its
        row lands; the long-lived service never needs this)."""
        with self._lock:
            pending = list(self._log_threads)
        end = time.monotonic() + timeout
        for t in pending:
            t.join(max(0.0, end - time.monotonic()))

    # ------------------------------------------------------------ the answer
    def answer(self, question, k=None, scope=None, agent=None, vector=True):
        k = int(k or self.k)
        text = (question or "").strip()
        out = {"query_hash": "", "degraded": False, "reason": "", "blocks": _empty_blocks(),
               "rendered": "", "ms": {b: 0.0 for b in BLOCKS}, "agent": agent, "k": k, "scope": scope}
        if len(text) < MIN_QUERY_CHARS:
            return out
        reasons = []
        keywords = fr.extract_keywords(text)
        have_facts = os.path.exists(self.facts_db)
        have_sm = os.path.exists(self.sm_db)
        if not have_facts:
            reasons.append("nodb:facts")
        if not have_sm:
            reasons.append("nodb:sm")
        run_vector = have_sm and vector
        if not vector:
            reasons.append("vector:off")
        else:
            if self._emb_state in ("unstarted", "failed"):
                self.ensure_embedder()
            if self._emb_state != "ready":          # reported even with no sm db: the caller must know
                reasons.append(f"embedder:{self._emb_state}")
                run_vector = False

        plan = []
        if have_facts and keywords:
            plan.append(("facts", lambda: self._query_facts(keywords, k, text)))
            plan.append(("worldview", lambda: self._query_worldview(keywords, k)))
        if run_vector:
            plan.append(("recall", lambda: self._query_recall(text, scope, k, agent)))
        with self._lock:
            self.queries += 1

        if plan:
            total_budget = max(self.budgets[b] for b, _ in plan) / 1000.0
            t_start = time.perf_counter()
            deadline = t_start + total_budget
            # ---- pool slot: the wait counts against the caller's budget (condition 1)
            with self._lock:
                self._queued += 1
            got = self._slots.acquire(timeout=total_budget)
            with self._lock:
                self._queued -= 1
            remaining = deadline - time.perf_counter()
            if not got or remaining <= 0.005:
                if got:
                    self._slots.release()
                reasons.insert(0, "queue")
                return self._finish(out, reasons, text, scope, agent, logged=False, vector=vector)
            with self._lock:
                self._inflight += 1
            results = {}
            events = {b: threading.Event() for b, _ in plan}
            pending = {"n": len(plan)}
            pend_lock = threading.Lock()

            def _release_when_all_done():
                with pend_lock:
                    pending["n"] -= 1
                    last = pending["n"] == 0
                if last:                            # slot is held until every worker is truly done
                    with self._lock:
                        self._inflight -= 1
                    self._slots.release()

            def _worker(name, fn):
                t0 = time.perf_counter()
                try:
                    results[name] = ("ok", fn())
                except Exception as e:   # noqa: BLE001
                    results[name] = ("err", e)
                finally:
                    results[name + ":ms"] = (time.perf_counter() - t0) * 1000.0
                    events[name].set()
                    _release_when_all_done()

            for name, fn in plan:
                threading.Thread(target=_worker, args=(name, fn), daemon=True,
                                 name=f"recall-{name}").start()
            for name, _ in plan:
                block_deadline = min(deadline, t_start + self.budgets[name] / 1000.0)
                events[name].wait(max(0.0, block_deadline - time.perf_counter()))
                if not events[name].is_set():
                    reasons.append(f"budget:{name}")
                    out["ms"][name] = round((time.perf_counter() - t_start) * 1000.0, 1)
                    continue
                out["ms"][name] = round(results.get(name + ":ms", 0.0), 1)
                status, val = results[name]
                if status == "err":
                    reasons.append(f"error:{name}")
                    log.error(f"recall {name} query error (non-fatal): {val}")
                    continue
                if name == "recall":
                    out["blocks"]["recall"], out["query_hash"] = val
                else:
                    out["blocks"][name] = val
        # the vector worker logs its own row when it finishes — even past its budget — so only a
        # query whose vector path never ran gets a row from here (never a duplicate).
        logged = bool(out["query_hash"]) or any(n == "recall" for n, _ in plan)
        return self._finish(out, reasons, text, scope, agent, logged=logged, vector=vector)

    def _finish(self, out, reasons, text, scope, agent, logged, vector=True):
        if not logged and os.path.exists(self.sm_db) and "queue" not in reasons:
            out["query_hash"] = self._log_query(text, scope, agent)
        out["degraded"] = bool(reasons)
        out["reason"] = ";".join(reasons)
        if reasons:
            with self._lock:
                self.degraded_count += 1
        out["rendered"] = render(out["blocks"])
        self._log_call(out, text, vector)
        return out

    # ------------------------------------------------------------ per-call load log (review R1)
    def _ensure_calls_table(self):
        """Service-owned, additive table in semantic-memory.db; the library's tables are untouched."""
        scripts = str(ORCHESTRA_DIR / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import semantic_memory.db as smdb  # noqa: PLC0415
        con = smdb.connect(self.sm_db)
        try:
            con.execute(CALLS_SCHEMA)
            con.execute("CREATE INDEX IF NOT EXISTS ix_recall_calls_ts ON recall_calls(ts)")
            cols = {r[1] for r in con.execute("PRAGMA table_info(recall_calls)")}
            for ddl in CALLS_MIGRATIONS:                     # additive, idempotent: a pre-vector table gains the column
                if ddl.split(" ADD COLUMN ")[1].split()[0] not in cols:
                    con.execute(ddl)
            con.commit()
        finally:
            con.close()

    def _log_call(self, out, text, vector):
        """One recall_calls row per answered query — both paths, queue-degraded included — written
        off the request path (same daemon-thread pattern as the queries_log fallback row)."""
        if not os.path.exists(self.sm_db):
            return
        row = [out.get("query_hash") or "", out.get("agent"), time.time(), int(bool(out.get("degraded"))),
               out.get("reason") or "", out["ms"].get("facts", 0.0), out["ms"].get("worldview", 0.0),
               out["ms"].get("recall", 0.0), self.budgets["facts"], self.budgets["worldview"],
               self.budgets["recall"], self.source, len(text or ""), 1 if vector else 0]

        def _write():
            try:
                if not self._calls_table_ready:
                    self._ensure_calls_table()
                    self._calls_table_ready = True
                import semantic_memory.db as smdb  # noqa: PLC0415
                con = smdb.connect(self.sm_db)
                try:
                    con.execute("INSERT INTO recall_calls(query_hash,agent,ts,degraded,reason,ms_facts,ms_worldview,"
                                "ms_recall,budget_facts,budget_worldview,budget_recall,source,question_len,vector)"
                                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
                    con.commit()
                finally:
                    con.close()
            except Exception as e:   # noqa: BLE001
                log.error(f"recall_calls write failed (non-fatal): {e}")
        t = threading.Thread(target=_write, daemon=True, name="recall-calls-log")
        with self._lock:
            self._log_threads = [x for x in self._log_threads if x.is_alive()] + [t]
        t.start()

    # ------------------------------------------------------------ feedback (condition 2)
    def ack(self, query_hash, path, signal, agent=None):
        try:
            sr._import_sm()
            import semantic_memory.feedback as smfb  # noqa: PLC0415
            return smfb.ack(self.sm_db, query_hash, path, signal=signal, agent=agent)
        except Exception as e:   # noqa: BLE001
            log.error(f"ack error (non-fatal): {e}")
            return {"ok": False, "reason": f"error:{type(e).__name__}"}

    # ------------------------------------------------------------ daily prune (condition 3)
    def prune_tick(self, now=None):
        now = time.time() if now is None else now
        if self._last_prune is not None and (now - self._last_prune) < PRUNE_INTERVAL_S:
            return False
        self._last_prune = now
        if os.path.exists(self.sm_db):
            try:
                sr._import_sm()
                sr.prune_queries_log(self.sm_db, days=self.prune_ttl_days)
            except Exception as e:   # noqa: BLE001
                log.error(f"prune failed (non-fatal): {e}")
        self.prunes += 1
        return True

    # ------------------------------------------------------------ warm (boot + periodic, off the path)
    def warm(self):
        """One read-only pass over both keyword paths so a cold page cache never costs the first
        caller its block (Arturo's warm_cache: cold worldview measured 2.19 s on 2026-09-16; the
        live service lost WORLDVIEW to budget on its very first call). Returns ms, 0.0 on missing db
        or any error, never raises."""
        if not os.path.exists(self.facts_db):
            return 0.0
        t0 = time.perf_counter()
        try:
            self._query_facts(["__bootwarm__"], self.k)
            self._query_worldview(["__bootwarm__"], self.k)
        except Exception as e:   # noqa: BLE001
            log.error(f"warm error (non-fatal): {e}")
            return 0.0
        ms = round((time.perf_counter() - t0) * 1000.0, 1)
        with self._lock:
            self.warms += 1
            self.last_warm_ms = ms
        log.info(f"warm: facts+worldview in {ms:.0f}ms")
        return ms

    # ------------------------------------------------------------ nightly sweep (step 5 / condition 5)
    def _sweep_impl(self):
        """Run the sweep in a SHORT-LIVED CHILD (option b): the child loads its own
        embedder, sweeps, prints one JSON line, exits — the resident heap never grows (the in-process
        sweep of 2026-09-22 left the service at 553 MB flat). Test seams: sweep_resolve / sweep_scope_for
        are passed to the child as files; embed_fn (tests' fake embedder) => --fake-embed."""
        import subprocess  # noqa: PLC0415
        import tempfile  # noqa: PLC0415
        argv = list(SWEEP_CHILD_ARGV) + ["--sm-db", self.sm_db, "--nice", str(SWEEP_NICE)]
        tmpfiles = []
        try:
            if self._sweep_resolve is not None:
                paths = list(self._sweep_resolve())
                pf = tempfile.NamedTemporaryFile("w", suffix=".paths", delete=False); tmpfiles.append(pf.name)
                pf.write("\n".join(paths) + "\n"); pf.close()
                argv += ["--paths-file", pf.name]
                if self._sweep_scope_for is not None:
                    sf = tempfile.NamedTemporaryFile("w", suffix=".scopes.json", delete=False); tmpfiles.append(sf.name)
                    json.dump({p: self._sweep_scope_for(p) for p in paths}, sf); sf.close()
                    argv += ["--scope-json", sf.name]
            if self.embed_fn is not None:
                argv.append("--fake-embed")
            env = dict(os.environ, OMP_NUM_THREADS=os.environ.get("OMP_NUM_THREADS", "1"))
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            child_pid = proc.pid
            try:
                stdout, stderr = proc.communicate(timeout=SWEEP_CHILD_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                proc.kill(); stdout, stderr = proc.communicate()
                return {"error": f"child timeout after {SWEEP_CHILD_TIMEOUT_S}s", "mode": "child", "child_pid": child_pid}
            lines = (stdout or "").strip().splitlines()
            st = {}
            if lines:
                try:
                    st = json.loads(lines[-1])
                except ValueError:
                    st = {}
            if proc.returncode != 0:
                st["error"] = f"child exit {proc.returncode}: {st.get('error') or (stderr or '').strip()[-300:]}"
            st.setdefault("mode", "child"); st.setdefault("child_pid", child_pid)
            return st
        finally:
            for f in tmpfiles:
                try:
                    os.unlink(f)
                except OSError:
                    pass

    def run_sweep(self):
        """Run the index sweep NOW (child process; this thread only waits). Returns the stats dict;
        errors land in it, never raise."""
        t0 = time.perf_counter()
        try:
            st = dict(self._sweep_impl())
        except Exception as e:  # noqa: BLE001
            st = {"error": f"{type(e).__name__}: {e}", "mode": "child"}
        st["ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        self.last_sweep = st
        self.last_sweep_ts = time.time()
        if "error" in st:
            log.error(f"sweep FAILED (non-fatal, retried tomorrow): {st['error']}")
        else:
            with self._lock:
                self.sweeps += 1
            log.info(f"sweep: {st}")
        return st

    def _skip(self, reason, now, detail="", due=False):
        """Record WHY a tick did not sweep. A DUE sweep that does not run must name itself —
        an embedder-still-loading skip used to be indistinguishable from a tick with nothing to do (deferred
        stayed 0, nothing logged), which is a monitor lying by omission. Every reason is counted; only the
        DUE ones are logged, because only they mean something wanted to happen and did not."""
        with self._lock:
            self.sweep_skips[reason] = self.sweep_skips.get(reason, 0) + 1
            self.last_skip = {"reason": reason, "ts": now}
        if due:
            log.info(f"sweep SKIPPED ({reason}){detail} — due sweep did not run, retried next tick")
        return False

    def sweep_tick(self, now=None):
        """True when a sweep was ATTEMPTED this tick: once per UTC day at/after sweep_utc_hour (a boot
        after the hour catches up the same day), only with the embedder ready, never twice a day, and
        a failure is not retried until the next day (no hot loop). Every False path names its reason in
        health()["sweep"]["skips"]/["last_skip"]; see _skip."""
        now = time.time() if now is None else now
        if self._emb_state == "failed":             # the tick is the retry clock when no query arrives
            self.ensure_embedder()
        if not self.sweep_enabled:
            return self._skip("disabled", now)
        # due-ness FIRST: before this, an embedder-loading skip and a not-due tick were the same silent False
        due_at = now - (now % 86400) + self.sweep_utc_hour * 3600
        if now < due_at:
            return self._skip("not_due", now)
        if self._sweep_last_attempt is not None and self._sweep_last_attempt >= due_at:
            return self._skip("already_swept_today", now)
        if self._emb_state != "ready":              # DUE but the embedder is not up: named, counted, logged
            return self._skip("embedder_not_ready", now, f" state={self._emb_state}", due=True)
        load = _load_1m()
        if load >= self.sweep_load_max:            # never add embedding work to a loaded box; retry next tick
            with self._lock:
                self.sweep_deferred += 1
            return self._skip("load", now, f" 1-min load {load:.1f} >= {self.sweep_load_max:.0f}", due=True)
        self._sweep_last_attempt = now
        self.run_sweep()
        return True

    # ------------------------------------------------------------ health
    def health(self):
        with self._lock:
            pool = {"n": self.pool_n, "inflight": self._inflight, "queued": self._queued}
            queries, degraded = self.queries, self.degraded_count
        return {"ok": True, "embedder": self._emb_state,
                "embedder_attempts": self._emb_attempts, "embedder_last_error": self._emb_last_error,
                "pool": pool,
                "db": {"facts": self.facts_db, "sm": self.sm_db},
                "uptime_s": round(time.time() - self.started, 1), "pid": os.getpid(),
                "prunes": self.prunes, "last_prune": self._last_prune, "budgets_ms": self.budgets,
                "queries": queries, "degraded": degraded,
                "warms": self.warms, "last_warm_ms": self.last_warm_ms,
                "sweeps": self.sweeps, "last_sweep": self.last_sweep, "last_sweep_ts": self.last_sweep_ts,
                "sweep": {"enabled": self.sweep_enabled, "utc_hour": self.sweep_utc_hour,
                          "load_max": self.sweep_load_max, "deferred": self.sweep_deferred,
                          "skips": dict(self.sweep_skips), "last_skip": self.last_skip}}


def stats(sm_db, hours=24, exclude_prefixes=DEFAULT_EXCLUDE_PREFIXES, now=None):
    """Budget-exceeded rate per block over REAL calls in the window (review R1). Read-only."""
    now = time.time() if now is None else now
    since = now - hours * 3600.0
    out = {"window_h": hours, "since": since, "calls": 0, "real_calls": 0, "degraded": 0, "queue": 0,
           "by_block": {b: {"exceeded": 0, "pct": 0.0} for b in BLOCKS}, "over_threshold": {},
           "threshold_pct": STATS_THRESHOLD_PCT, "agents": {}, "excluded_agents": [], "first_real_call_ts": None,
           "sources": {}, "vector_calls": 0}
    if not os.path.exists(sm_db):
        return out
    import sqlite3  # noqa: PLC0415
    con = sqlite3.connect(f"file:{sm_db}?mode=ro", uri=True)
    try:
        try:
            rows = con.execute("SELECT agent, ts, degraded, reason, source, vector FROM recall_calls WHERE ts >= ?"
                               " ORDER BY ts", [since]).fetchall()
        except sqlite3.OperationalError:          # table not created yet: nothing logged
            return out
    finally:
        con.close()
    excluded = set()
    for agent, ts, degraded, reason, source, vector in rows:
        out["calls"] += 1
        a = agent or "unknown"
        if any(a.startswith(p) for p in exclude_prefixes):
            excluded.add(a)
            continue
        out["real_calls"] += 1
        out["agents"][a] = out["agents"].get(a, 0) + 1
        out["sources"][source or "?"] = out["sources"].get(source or "?", 0) + 1
        out["vector_calls"] = out.get("vector_calls", 0) + (1 if vector else 0)
        if out["first_real_call_ts"] is None:
            out["first_real_call_ts"] = ts
        if degraded:
            out["degraded"] += 1
        tags = set((reason or "").split(";"))
        if "queue" in tags:
            out["queue"] += 1
        for b in BLOCKS:
            if f"budget:{b}" in tags:
                out["by_block"][b]["exceeded"] += 1
    n = out["real_calls"]
    for b in BLOCKS:
        pct = round(out["by_block"][b]["exceeded"] / n * 100.0, 1) if n else 0.0
        out["by_block"][b]["pct"] = pct
        out["over_threshold"][b] = pct > STATS_THRESHOLD_PCT
    out["excluded_agents"] = sorted(excluded)
    return out
