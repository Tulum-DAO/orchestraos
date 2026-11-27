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
MAX_FTS_TERMS = 32

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
)


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


def sync(facts_path=None, index_path=None, embed=None, batch=256, max_rows=None, threads=BUILD_THREADS):
    """Embed and FTS-index every facts.db row past the last one indexed. Resumable: each batch commits on its own.

    Returns {"added", "indexed", "source_rows", "rebuilt"}. A facts.db whose top rowid fell below the index's
    (a restore or a rebuilt store) drops the index and starts again, since its rowids no longer mean the same
    rows. A fact_id that comes back under a new rowid replaces its old entry."""
    facts_path = facts_path or default_facts_path()
    index_path = index_path or default_index_path()
    if embed is None:
        embed = fastembed_embedder(min(int(threads), BUILD_THREADS))
    src = _open_ro(facts_path)
    con = _open_rw(index_path)
    rebuilt = False
    added = 0
    try:
        top_src = src.execute("SELECT COALESCE(MAX(rowid), 0) FROM facts").fetchone()[0]
        mark = con.execute("SELECT COALESCE(MAX(src_rowid), 0) FROM doc").fetchone()[0]
        if mark > top_src:
            con.execute("DELETE FROM doc")
            con.execute("DELETE FROM fts")
            con.commit()
            mark, rebuilt = 0, True
        _set_meta(con, "model", MODEL)
        _set_meta(con, "dim", DIM)
        con.commit()
        while max_rows is None or added < max_rows:
            n = batch if max_rows is None else min(batch, max_rows - added)
            rows = src.execute("SELECT rowid, fact_id, text FROM facts WHERE rowid > ? ORDER BY rowid LIMIT ?",
                               (mark, n)).fetchall()
            if not rows:
                break
            vecs = embed([t or "" for _, _, t in rows])
            with con:
                for (rid, fid, txt), v in zip(rows, vecs):
                    old = con.execute("SELECT rowid FROM doc WHERE fact_id=?", (fid,)).fetchone()
                    if old:
                        con.execute("DELETE FROM fts WHERE rowid=?", old)
                        con.execute("DELETE FROM doc WHERE rowid=?", old)
                    cur = con.execute("INSERT INTO doc(fact_id, src_rowid, vec) VALUES (?, ?, ?)",
                                      (fid, rid, to_blob(v)))
                    con.execute("INSERT INTO fts(rowid, text) VALUES (?, ?)", (cur.lastrowid, txt or ""))
                _set_meta(con, "synced_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            added += len(rows)
            mark = rows[-1][0]
        if added:
            con.execute("INSERT INTO fts(fts) VALUES('optimize')")
            con.commit()
        indexed = con.execute("SELECT COUNT(*) FROM doc").fetchone()[0]
        return {"added": added, "indexed": indexed, "source_rows": top_src, "rebuilt": rebuilt}
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

def fts_match(text):
    """An FTS5 OR-query over the topic words of `text` (quoted, so no word is read as an operator), or ''."""
    seen = []
    for t in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(t) < 2 or t in _STOP or t in seen:
            continue
        seen.append(t)
        if len(seen) >= MAX_FTS_TERMS:
            break
    return " OR ".join(f'"{t}"' for t in seen)


def rrf(rank_lists, k=RRF_K):
    """Reciprocal-rank fusion: {id: sum 1/(k + rank)} over every list an id appears in (rank from 1)."""
    sc = {}
    for lst in rank_lists:
        for r, d in enumerate(lst, 1):
            sc[d] = sc.get(d, 0.0) + 1.0 / (k + r)
    return sc


class _Matrix:
    """The vectors held in memory, appended to as sync adds rows. Brute-force dot products: 50k x 384 is a few ms."""

    def __init__(self, path):
        self.path = path
        self.ident = None
        self.top = 0
        self.ids = []
        self.mat = None

    def _file_ident(self):
        st = os.stat(self.path)
        return (st.st_dev, st.st_ino)

    def refresh(self, max_inline=None):
        """Load rows past the last loaded rowid. Returns False when more than max_inline rows are waiting (the
        caller then reloads off the turn path) or when the file was replaced (a rebuild starts from zero)."""
        np = _np()
        ident = self._file_ident()
        if ident != self.ident:
            self.ident, self.top, self.ids, self.mat = ident, 0, [], np.zeros((0, DIM), dtype=np.float32)
            if max_inline is not None:
                return False
        con = _open_ro(self.path)
        try:
            top = con.execute("SELECT COALESCE(MAX(rowid), 0) FROM doc").fetchone()[0]
            if top < self.top:                       # rows were deleted under us (a rebuild in place)
                self.top, self.ids, self.mat = 0, [], np.zeros((0, DIM), dtype=np.float32)
            if top == self.top:
                return True
            if max_inline is not None:
                waiting = con.execute("SELECT COUNT(*) FROM doc WHERE rowid > ?", (self.top,)).fetchone()[0]
                if waiting > max_inline:
                    return False
            rows = con.execute("SELECT rowid, fact_id, vec FROM doc WHERE rowid > ? ORDER BY rowid",
                               (self.top,)).fetchall()
        finally:
            con.close()
        if rows:
            # a fact_id re-indexed under a new rowid leaves its old row in the matrix; search drops duplicates
            self.ids.extend(r[1] for r in rows)
            self.mat = np.vstack([self.mat, from_blobs([r[2] for r in rows])])
            self.top = rows[-1][0]
        return True


_lock = threading.Lock()
_state = {"embed": None, "matrix": None, "loading": False, "failed": None}
MAX_INLINE_ROWS = 2000       # a turn absorbs at most this many freshly synced vectors; more go to the loader


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
        log.info(f"facts-index: ready, {len(m.ids)} vectors")
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
    return bool(_state["embed"] and m is not None and m.path == index_path and m.ids)


def min_cos():
    try:
        return float(os.environ.get(MIN_COS_ENV, DEFAULT_MIN_COS))
    except ValueError:
        return DEFAULT_MIN_COS


def search(text, index_path=None, depth=DEPTH, embed_factory=None):
    """The two ranked lists for `text`: (fts_fact_ids, vector_fact_ids), each up to `depth` long, best first.
    A vector hit below min_cos() is left out: the nearest neighbours of an unrelated question are not answers.

    Returns None while the index is not usable (missing file, model or matrix still loading, load failed);
    the first such call starts the background loader. Never loads a model or the full matrix on the caller's
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
    if not m.refresh(max_inline=MAX_INLINE_ROWS):
        _start_loader(index_path, embed_factory)
        return None
    np = _np()
    qv = np.frombuffer(to_blob(_state["embed"]([text])[0]), dtype="<f4")
    sims = m.mat @ qv
    n = min(len(sims), depth * 2)                    # headroom for duplicate ids from re-indexed facts
    part = np.argpartition(-sims, n - 1)[:n] if n < len(sims) else np.arange(len(sims))
    vec_ids = []
    floor = min_cos()
    for i in part[np.argsort(-sims[part])]:
        if sims[i] < floor:
            break
        fid = m.ids[int(i)]
        if fid not in vec_ids:
            vec_ids.append(fid)
        if len(vec_ids) >= depth:
            break
    fts_ids = []
    q = fts_match(text)
    if q:
        con = _open_ro(index_path)
        try:
            fts_ids = [r[0] for r in con.execute(
                "SELECT doc.fact_id FROM fts JOIN doc ON doc.rowid = fts.rowid WHERE fts MATCH ? "
                "ORDER BY bm25(fts) LIMIT ?", (q, depth))]
        finally:
            con.close()
    return fts_ids, vec_ids


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
