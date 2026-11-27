"""Hybrid facts index: FTS5 bm25 + local MiniLM vectors over state/brain/facts.db (plan step 1).

Shaw chose option B on card apr_39e1b560 (2026-11-26): all-MiniLM-L6-v2 plus FTS5 bm25, rank-fused, no LLM
call on any lookup. Measured on this box: query embed p95 22 ms, end-to-end recall p95 62 ms on a quiet box,
28/30 reworded facts in the top 5 (FTS alone: 24/30). See state/projects/nod/PLAN_one-memory-system.md.

What lives where:
  - facts.db stays the source of truth and is only ever READ (mode=ro). Its states, supersession and noise
    filters are applied at query time by facts_recall, on the rows this index points at.
  - The index is a SIDECAR, $ORCHESTRA_DIR/state/brain/facts_index.db: one `doc` row per fact (fact_id,
    the facts.db rowid it came from, its 384-d float32 vector), and an FTS5 table over the same text with the
    same rowid. A missing, partial or stale sidecar is never an error: callers fall back to keyword recall.
  - `sync` is incremental and resumable: it embeds the facts.db rows past the last rowid it holds, in batches,
    one transaction per batch. The first build is the same command run over every row (~2.7 h on 2 cores).
    This relies on fact text being append-only: as of 2026-11-27 the ingest code (scripts/brain) only ever
    UPDATEs state, expiry, slot and validity columns, and never deletes a fact or rewrites its text. States
    are read live at query time, so they need no sync.

CPU (fleet rule, gm msg_9b1b7757): every build or sync runs as
    flock /tmp/heavy-job.lock nice -n 19 taskset -c 6,7 python3 -m services.arturo.facts_index sync
and `sync` itself also lowers its priority, pins to cores 6,7 and caps the embedder at 2 threads, so a bare
invocation cannot take the box either. The lock is the caller's: flock is per open file, so taking it again
in here would deadlock behind the wrapper's own hold.

Query side (the voice proxy): the embedder and the vector matrix load in ONE background thread on first use,
never on a turn. Until both are ready `search` returns None and the turn gets today's keyword answer.

Optional dependencies: numpy (already a requirement) and fastembed (not shipped by the public tree). With
fastembed absent the index is simply never ready.
"""
import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("arturo-facts-index")

FLAG = "ARTURO_FACTS_HYBRID"
MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 384
DEPTH = 50                   # rows each ranker contributes to the fusion (the measured setting)
RRF_K = 60                   # reciprocal-rank constant (the measured setting)
MIN_COS_ENV = "ARTURO_FACTS_HYBRID_MIN_COS"
DEFAULT_MIN_COS = 0.33       # the measured same-meaning threshold for MiniLM (98.3% on 30 pairs + 30 negatives)
BUILD_THREADS = 2            # gm msg_9b1b7757: embedding threads capped at 2
QUERY_THREADS = 1            # in the proxy: one intra-op thread, like semantic_recall
HEAVY_CORES = (6, 7)
MAX_FTS_TERMS = 8            # the rarest topic words of a question; more only widens the bm25 sort
MIN_FTS_TERMS = 3            # a short question keeps its 3 rarest words even when they are common
FTS_POOL = 100               # bm25 candidates re-ranked by how many of the question's keywords they hold
DF_MAX_FRAC = 0.02           # a word in more than 2% of facts says little and makes the match set huge
DF_MIN_CAP = 200             # ...but on a small store, never drop a word found in fewer facts than this

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Question scaffolding dropped before the FTS match; the vector side always sees the whole text.
_STOP = set(
    "a an the and or but if of to in on at by for with from is are was were be been being it its this that these "
    "those i me my we our you your he she they them his her their do does did done have has had what whats which "
    "who whom when where why how not no so as than then there here can could would should will just also about "
    "into up out over tell show know".split())


def orchestra_dir():
    return Path(os.environ.get("ORCHESTRA_DIR", str(_REPO_ROOT)))


def default_index_path():
    return str(orchestra_dir() / "state" / "brain" / "facts_index.db")


def default_facts_path():
    return str(orchestra_dir() / "state" / "brain" / "facts.db")


def enabled():
    return os.environ.get(FLAG, "") == "1"


# --- vectors -------------------------------------------------------------------------------------------------

def _np():
    import numpy as np  # noqa: PLC0415 — optional on the query path until a search runs
    return np


def to_blob(vec):
    """Little-endian float32, L2-normalised, len DIM*4: the byte contract semantic_memory.embed uses."""
    np = _np()
    a = np.asarray(vec, dtype="<f4").ravel()
    n = float(np.linalg.norm(a))
    if n:
        a = a / n
    return a.astype("<f4").tobytes()


def from_blobs(blobs):
    np = _np()
    if not blobs:
        return np.zeros((0, DIM), dtype=np.float32)
    return np.frombuffer(b"".join(blobs), dtype="<f4").reshape(len(blobs), -1).astype(np.float32)


def fastembed_embedder(threads):
    """Return embed(list[str]) -> list[vector] backed by fastembed MiniLM, or raise ImportError."""
    from fastembed import TextEmbedding  # noqa: PLC0415
    kw = {"model_name": MODEL, "threads": threads}
    cache = os.environ.get("FASTEMBED_CACHE_PATH") or os.path.expanduser("~/.cache/fastembed")
    if os.path.isdir(cache):
        kw["cache_dir"] = cache
    model = TextEmbedding(**kw)

    def embed(texts):
        return list(model.embed(list(texts), batch_size=64))
    return embed


# --- index schema --------------------------------------------------------------------------------------------

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS doc (rowid INTEGER PRIMARY KEY, fact_id TEXT NOT NULL UNIQUE, "
    "src_rowid INTEGER NOT NULL, vec BLOB NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_doc_src ON doc(src_rowid)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, tokenize='porter unicode61')",
    # how many facts hold each word: picks the rare words of a question for the FTS match (see fts_match)
    "CREATE TABLE IF NOT EXISTS df (term TEXT PRIMARY KEY, n INTEGER NOT NULL) WITHOUT ROWID",
)


def terms(text):
    """The distinct topic words of `text`, in order: lowercase alphanumeric runs, 2+ chars, no stopwords."""
    seen = []
    for t in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(t) >= 2 and t not in _STOP and t not in seen:
            seen.append(t)
    return seen


def _df_add(con, texts, sign=1):
    counts = {}
    for txt in texts:
        for t in terms(txt):
            counts[t] = counts.get(t, 0) + sign
    con.executemany("INSERT INTO df(term, n) VALUES (?, ?) ON CONFLICT(term) DO UPDATE SET n = n + excluded.n",
                    counts.items())


def _df_backfill(con):
    """Count words over everything already indexed, once (an index built before the df table existed)."""
    if _meta(con, "df_built") == "1":
        return
    with con:
        con.execute("DELETE FROM df")
        cur = con.execute("SELECT text FROM fts")
        while True:
            chunk = [r[0] for r in cur.fetchmany(5000)]
            if not chunk:
                break
            _df_add(con, chunk)
        _set_meta(con, "df_built", "1")


def _open_rw(index_path):
    os.makedirs(os.path.dirname(index_path) or ".", exist_ok=True)
    con = sqlite3.connect(index_path, timeout=30.0)
    con.execute("PRAGMA journal_mode=WAL")
    for s in _SCHEMA:
        con.execute(s)
    return con


def _open_ro(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
    con.execute("PRAGMA query_only=ON")
    return con


def _meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _set_meta(con, key, value):
    con.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))


# --- build / sync --------------------------------------------------------------------------------------------

def _yield_cpu():
    """Lowest priority, cores 6,7, whatever the caller forgot. Best effort; never fails a sync."""
    try:
        os.nice(19 - os.nice(0))
    except OSError:
        pass
    try:
        cores = set(HEAVY_CORES) & set(range(os.cpu_count() or 1))
        if cores:
            os.sched_setaffinity(0, cores)
    except (AttributeError, OSError):
        pass


def _drop_doc(con, doc_rowid):
    _df_add(con, [r[0] for r in con.execute("SELECT text FROM fts WHERE rowid=?", (doc_rowid,))], -1)
    con.execute("DELETE FROM fts WHERE rowid=?", (doc_rowid,))
    con.execute("DELETE FROM doc WHERE rowid=?", (doc_rowid,))


def _walk_back(con, src):
    """Drop indexed rows, from the top down, whose facts.db row is gone or is now a different fact, and stop at the
    first that still matches. SQLite reuses a deleted TOP rowid, so without this a fact deleted and another added
    after it would leave the old one indexed and skip the new one. Lower rowids are never reused; a fact deleted
    below the top only leaves a stale index entry, which facts_recall drops on the live row. A replaced store
    walks back to empty, which is a rebuild."""
    removed = 0
    while True:
        row = con.execute("SELECT rowid, fact_id, src_rowid FROM doc ORDER BY src_rowid DESC LIMIT 1").fetchone()
        if row is None:
            return removed
        live = src.execute("SELECT fact_id FROM facts WHERE rowid=?", (row[2],)).fetchone()
        if live is not None and live[0] == row[1]:
            return removed
        _drop_doc(con, row[0])
        removed += 1


def _bump_generation(con):
    _set_meta(con, "generation", int(_meta(con, "generation", "0")) + 1)


def sync(facts_path=None, index_path=None, embed=None, batch=256, max_rows=None, threads=BUILD_THREADS):
    """Embed and FTS-index every facts.db row past the last one indexed. Resumable: each batch commits on its own.

    Returns {"added", "indexed", "source_rows", "rebuilt", "removed", "complete"}. Before going forward it walks
    back over indexed rows facts.db no longer holds (see _walk_back). Meta `generation` changes whenever an indexed
    row is removed, so a reader reloads instead of appending; meta `complete` is '1' once the index has caught up
    with facts.db and '0' while a build from empty is still under way, and search serves nothing until it is '1'.
    A fact_id that comes back under a new rowid replaces its old entry."""
    facts_path = facts_path or default_facts_path()
    index_path = index_path or default_index_path()
    if embed is None:
        embed = fastembed_embedder(min(int(threads), BUILD_THREADS))
    src = _open_ro(facts_path)
    con = _open_rw(index_path)
    added = 0
    try:
        top_src = src.execute("SELECT COALESCE(MAX(rowid), 0) FROM facts").fetchone()[0]
        _df_backfill(con)
        with con:
            removed = _walk_back(con, src)
            if removed:
                _bump_generation(con)
            mark = con.execute("SELECT COALESCE(MAX(src_rowid), 0) FROM doc").fetchone()[0]
            if mark == 0 and top_src:
                _set_meta(con, "complete", "0")
            _set_meta(con, "model", MODEL)
            _set_meta(con, "dim", DIM)
        rebuilt = bool(removed) and mark == 0
        caught_up = False
        while max_rows is None or added < max_rows:
            n = batch if max_rows is None else min(batch, max_rows - added)
            rows = src.execute("SELECT rowid, fact_id, text FROM facts WHERE rowid > ? ORDER BY rowid LIMIT ?",
                               (mark, n)).fetchall()
            if not rows:
                caught_up = True
                break
            vecs = embed([t or "" for _, _, t in rows])
            with con:
                replaced = False
                for (rid, fid, txt), v in zip(rows, vecs):
                    old = con.execute("SELECT rowid FROM doc WHERE fact_id=?", (fid,)).fetchone()
                    if old:
                        _drop_doc(con, old[0])
                        replaced = True
                    cur = con.execute("INSERT INTO doc(fact_id, src_rowid, vec) VALUES (?, ?, ?)",
                                      (fid, rid, to_blob(v)))
                    con.execute("INSERT INTO fts(rowid, text) VALUES (?, ?)", (cur.lastrowid, txt or ""))
                _df_add(con, [t for _, _, t in rows])
                if replaced:
                    _bump_generation(con)
                _set_meta(con, "synced_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            added += len(rows)
            mark = rows[-1][0]
        if not caught_up:
            caught_up = src.execute("SELECT 1 FROM facts WHERE rowid > ? LIMIT 1", (mark,)).fetchone() is None
        with con:
            if caught_up:
                _set_meta(con, "complete", "1")
            if added:
                con.execute("INSERT INTO fts(fts) VALUES('optimize')")
        indexed = con.execute("SELECT COUNT(*) FROM doc").fetchone()[0]
        return {"added": added, "indexed": indexed, "source_rows": top_src, "rebuilt": rebuilt,
                "removed": removed, "complete": _meta(con, "complete") == "1"}
    finally:
        con.close()
        src.close()


def status(facts_path=None, index_path=None):
    facts_path = facts_path or default_facts_path()
    index_path = index_path or default_index_path()
    out = {"index": index_path, "exists": os.path.exists(index_path)}
    if out["exists"]:
        con = _open_ro(index_path)
        try:
            out["indexed"] = con.execute("SELECT COUNT(*) FROM doc").fetchone()[0]
            out["mark"] = con.execute("SELECT COALESCE(MAX(src_rowid), 0) FROM doc").fetchone()[0]
            out["synced_at"] = _meta(con, "synced_at")
            out["complete"] = _meta(con, "complete") == "1"
            out["generation"] = int(_meta(con, "generation", "0"))
            out["model"] = _meta(con, "model")
        finally:
            con.close()
    if os.path.exists(facts_path):
        src = _open_ro(facts_path)
        try:
            out["source_rows"] = src.execute("SELECT COALESCE(MAX(rowid), 0) FROM facts").fetchone()[0]
        finally:
            src.close()
    return out


# --- search --------------------------------------------------------------------------------------------------

def fts_match(text, df=None, total=0):
    """An FTS5 OR-query over the topic words of `text` (each quoted, so no word is read as an operator), or ''.

    With the word counts (`df`: word -> facts holding it; `total`: facts indexed), only words some fact holds are
    kept, words held by more than DF_MAX_FRAC of facts are dropped (a short question keeps its MIN_FTS_TERMS
    rarest), and the MAX_FTS_TERMS rarest remain. Measured 2026-11-27 on 30 real voice questions: up to 32 words,
    common ones included ("make", "get"), put the bm25 sort at p95 133 ms; the rare words carry the match, and
    the vectors carry the meaning."""
    words = terms(text)
    if df is not None and words:
        cap = max(DF_MIN_CAP, int(total * DF_MAX_FRAC))
        held = sorted((w for w in words if df.get(w, 0) > 0), key=lambda w: df[w])
        words = [w for w in held if df[w] <= cap]
        if len(words) < MIN_FTS_TERMS:
            words = held[:MIN_FTS_TERMS]
    return " OR ".join(f'"{t}"' for t in words[:MAX_FTS_TERMS])


def rrf(rank_lists, k=RRF_K):
    """Reciprocal-rank fusion: {id: sum 1/(k + rank)} over every list an id appears in (rank from 1)."""
    sc = {}
    for lst in rank_lists:
        for r, d in enumerate(lst, 1):
            sc[d] = sc.get(d, 0.0) + 1.0 / (k + r)
    return sc


def _trim_heap():
    """Hand freed heap back to the OS after the big first load (glibc keeps it otherwise). Best effort."""
    try:
        import ctypes  # noqa: PLC0415
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:                                # noqa: BLE001 — not glibc, or no ctypes: nothing to trim
        pass


class _Snap:
    """One consistent view for a lookup: the vectors and the ids they belong to, the in-memory FTS copy, the word
    counts, and the sidecar's generation and completeness. Never changed once published; refresh publishes a new
    one. An incremental refresh shares the FTS copy and appends to it (under mlock): an older view that sees the
    newer rows there only sees more real facts."""
    __slots__ = ("gen", "complete", "top", "ids", "mat", "mem", "mlock", "df")

    def __init__(self, gen, complete, top, ids, mat, mem, mlock, df):
        self.gen, self.complete, self.top, self.ids, self.mat = gen, complete, top, ids, mat
        self.mem, self.mlock, self.df = mem, mlock, df


def _new_mem():
    mem = sqlite3.connect(":memory:", check_same_thread=False)
    mem.execute("CREATE VIRTUAL TABLE m USING fts5(text, fid UNINDEXED, tokenize='porter unicode61')")
    return mem


class _Matrix:
    """What a lookup reads, held in memory and appended to as sync adds rows: the vectors (brute-force dot
    products, 50k x 384 is a few ms), an in-memory copy of the FTS table and the word counts. The sidecar file is
    only checked for new rows; measured 2026-11-27, the same FTS query costs p95 ~10 ms in memory against ~35 ms
    on the file, under the same load.

    Lookups read `snap` once and use only that; refreshes run one at a time and replace it whole. Any row sync
    removed (generation changed) or a replaced file means a full reload on the loader thread."""

    def __init__(self, path):
        self.path = path
        self.ident = None
        self.checked = 0.0                           # monotonic time of the last refresh check (see search)
        self.snap = None
        self.rlock = threading.Lock()

    def _file_ident(self):
        st = os.stat(self.path)
        return (st.st_dev, st.st_ino)

    def refresh(self, max_inline=None):
        """Publish a snapshot with every row the sidecar holds. A lookup (max_inline set) only appends up to
        max_inline new rows; it returns False when a full reload is due (more rows waiting, rows removed, the file
        replaced) so the caller hands that to the loader. A lookup never waits behind another refresh."""
        if not self.rlock.acquire(blocking=max_inline is None):
            return True
        try:
            return self._refresh(max_inline)
        finally:
            self.rlock.release()

    def _refresh(self, max_inline):
        np = _np()
        ident = self._file_ident()
        old = self.snap
        con = _open_ro(self.path)
        try:
            con.execute("BEGIN")                     # one snapshot for the counts and the rows, whatever sync does
            gen = _meta(con, "generation", "0")
            complete = _meta(con, "complete") == "1"
            top = con.execute("SELECT COALESCE(MAX(rowid), 0) FROM doc").fetchone()[0]
            full = old is None or ident != self.ident or gen != old.gen or top < old.top
            if not full and top == old.top:
                if complete != old.complete:
                    self.snap = _Snap(gen, complete, old.top, old.ids, old.mat, old.mem, old.mlock, old.df)
                return True
            base = 0 if full else old.top
            n_new = con.execute("SELECT COUNT(*) FROM doc WHERE rowid > ?", (base,)).fetchone()[0]
            if max_inline is not None and (full or n_new > max_inline):
                return False
            if full:
                mem, mlock, df = _new_mem(), threading.Lock(), dict(con.execute("SELECT term, n FROM df"))
            else:
                mem, mlock, df = old.mem, old.mlock, dict(old.df)
            cur = con.execute("SELECT doc.rowid, doc.fact_id, doc.vec, fts.text FROM doc JOIN fts "
                              "ON fts.rowid = doc.rowid WHERE doc.rowid > ? ORDER BY doc.rowid", (base,))
            # streamed in chunks into one preallocated block: the first load is 50k rows, and holding every row
            # (vector blobs + text) at once before copying them doubled the proxy's memory for nothing
            block = np.empty((n_new, DIM), dtype=np.float32)
            ids, filled, last = [], 0, base
            while filled < n_new:
                chunk = cur.fetchmany(4096)[:n_new - filled]
                if not chunk:
                    break
                block[filled:filled + len(chunk)] = from_blobs([r[2] for r in chunk])
                filled += len(chunk)
                ids.extend(r[1] for r in chunk)
                if not full:
                    for r in chunk:
                        for t in terms(r[3]):
                            df[t] = df.get(t, 0) + 1
                with mlock:
                    mem.executemany("INSERT INTO m(rowid, text, fid) VALUES (?, ?, ?)",
                                    ((r[0], r[3] or "", r[1]) for r in chunk))
                last = chunk[-1][0]
        finally:
            con.close()
        if full:
            with mlock:
                mem.execute("INSERT INTO m(m) VALUES('optimize')")
            mat, all_ids = block[:filled], ids
        else:
            mat, all_ids = np.vstack([old.mat, block[:filled]]), old.ids + ids
        # a fact_id re-indexed under a new rowid can leave its old row here until the next full load; search
        # drops duplicates, and the live facts.db row decides what is served
        self.ident = ident
        self.snap = _Snap(gen, complete, last, all_ids, mat, mem, mlock, df)
        if full:
            _trim_heap()
        return True


def _snap_fts(snap, text, depth, keywords=None):
    """The FTS list (see search): bm25 candidates re-ranked by how many keywords each holds."""
    q = fts_match(text, snap.df, len(snap.ids))
    if not q:
        return []
    with snap.mlock:
        rows = snap.mem.execute("SELECT fid, text FROM m WHERE m MATCH ? ORDER BY bm25(m) LIMIT ?",
                                (q, max(depth, FTS_POOL))).fetchall()
    kws = keywords if keywords else terms(text)
    held = [sum(1 for k in kws if k in (t or "").lower()) for _, t in rows]
    order = sorted(range(len(rows)), key=lambda i: (-held[i], i))    # stable: bm25 order within a count
    out = []
    for i in order:
        if rows[i][0] not in out:
            out.append(rows[i][0])
        if len(out) >= depth:
            break
    return out


_lock = threading.Lock()
_state = {"embed": None, "matrix": None, "loading": False, "failed": None}
MAX_INLINE_ROWS = 2000       # a turn absorbs at most this many freshly synced vectors; more go to the loader
REFRESH_EVERY_S = 30.0       # how often a lookup checks the sidecar for newly synced rows


def _reset_for_tests():
    with _lock:
        _state.update(embed=None, matrix=None, loading=False, failed=None)


def _loader(index_path, embed_factory):
    try:
        os.environ.setdefault("OMP_NUM_THREADS", str(QUERY_THREADS))
        embed = _state["embed"] or embed_factory()
        embed(["warmup: facts hybrid recall"])
        m = _state["matrix"] if (_state["matrix"] and _state["matrix"].path == index_path) else _Matrix(index_path)
        m.refresh()
        with _lock:
            _state.update(embed=embed, matrix=m, failed=None)
        log.info(f"facts-index: ready, {len(m.snap.ids) if m.snap else 0} vectors")
    except Exception as e:                           # noqa: BLE001 — absent model / index = keyword recall
        with _lock:
            _state["failed"] = str(e)
        log.error(f"facts-index loader failed, recall stays keyword-only until the next try: {e}")
    finally:
        with _lock:
            _state["loading"] = False


def _start_loader(index_path, embed_factory):
    with _lock:
        if _state["loading"]:
            return
        _state["loading"] = True
    threading.Thread(target=_loader, args=(index_path, embed_factory), daemon=True,
                     name="facts-index-loader").start()


def ready(index_path=None):
    index_path = index_path or default_index_path()
    m = _state["matrix"]
    return bool(_state["embed"] and m is not None and m.path == index_path and m.snap is not None)


def min_cos():
    try:
        return float(os.environ.get(MIN_COS_ENV, DEFAULT_MIN_COS))
    except ValueError:
        return DEFAULT_MIN_COS


def search(text, index_path=None, depth=DEPTH, embed_factory=None, keywords=None):
    """The two ranked lists for `text`: (fts_fact_ids, vector_fact_ids), each up to `depth` long, best first.
    A vector hit below min_cos() is left out: the nearest neighbours of an unrelated question are not answers.
    The FTS list is the FTS_POOL best bm25 rows re-ranked by how many of `keywords` (default: the topic words
    of `text`) each one holds, bm25 breaking ties: keyword recall's own signal, over a better candidate set.

    Returns None while the index is not usable (missing file, model or matrix still loading, load failed, the
    sidecar empty or mid-rebuild); the first such call starts the background loader. Never loads a model or the full matrix on the caller's
    thread."""
    index_path = index_path or default_index_path()
    if not os.path.exists(index_path):
        return None
    if embed_factory is None:
        def embed_factory():
            return fastembed_embedder(QUERY_THREADS)
    if not ready(index_path):
        _start_loader(index_path, embed_factory)
        return None
    m = _state["matrix"]
    now = time.monotonic()
    if now - m.checked >= REFRESH_EVERY_S:           # new facts land twice a day; no need to look on every turn
        m.checked = now
        if not m.refresh(max_inline=MAX_INLINE_ROWS):
            m.checked = 0.0
            _start_loader(index_path, embed_factory)
            return None
    snap = m.snap
    if snap is None or not snap.complete or not snap.ids:
        return None                                  # a partial index would miss facts keyword recall finds
    fts_job = _fts_pool().submit(_snap_fts, snap, text, depth, keywords)   # sqlite and onnx both release the GIL
    np = _np()
    qv = np.frombuffer(to_blob(_state["embed"]([text])[0]), dtype="<f4")
    sims = snap.mat @ qv
    n = min(len(sims), depth * 2)                    # headroom for duplicate ids from re-indexed facts
    part = np.argpartition(-sims, n - 1)[:n] if n < len(sims) else np.arange(len(sims))
    vec_ids = []
    floor = min_cos()
    for i in part[np.argsort(-sims[part])]:
        if sims[i] < floor:
            break
        fid = snap.ids[int(i)]
        if fid not in vec_ids:
            vec_ids.append(fid)
        if len(vec_ids) >= depth:
            break
    return fts_job.result(), vec_ids


_pool = []


def _fts_pool():
    with _lock:
        if not _pool:
            from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415
            _pool.append(ThreadPoolExecutor(max_workers=1, thread_name_prefix="facts-index-fts"))
        return _pool[0]



# --- CLI -----------------------------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="facts_index", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="embed + FTS-index facts.db rows not yet indexed (run under the heavy-job lock)")
    s.add_argument("--facts", default=None)
    s.add_argument("--index", default=None)
    s.add_argument("--batch", type=int, default=256)
    s.add_argument("--max-rows", type=int, default=None, help="stop after this many rows (the next sync resumes)")
    s.add_argument("--threads", type=int, default=BUILD_THREADS)
    st = sub.add_parser("status", help="rows indexed vs rows in facts.db")
    st.add_argument("--facts", default=None)
    st.add_argument("--index", default=None)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.cmd == "status":
        print(json.dumps(status(a.facts, a.index)))
        return 0
    _yield_cpu()
    t0 = time.time()
    out = sync(a.facts, a.index, batch=a.batch, max_rows=a.max_rows, threads=a.threads)
    out["wall_s"] = round(time.time() - t0, 1)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
