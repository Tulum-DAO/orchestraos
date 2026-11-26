"""The brain fact bus: facts.db writer/reader.

PURE-ADDITIVE + INERT: nothing consumes this until it is wired in.
Guardrails (binding):
  - EVERY write runs under an exclusive flock on the bus's OWN lockfile
    (facts.lock next to the db — registry_lock.py pattern, its own file).
  - Ingest is idempotent by provenance hash (fact_id) — INSERT OR IGNORE.
  - Append-only + state column: rows are never deleted; lifecycle changes
    flip `state` only.
  - ingest_meta records {last_source_event_ts, last_ingest_at} per
    source_kind — the substrate of the load-bearing freshness header.
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:                      # its sibling modules import flat, also when loaded by file path
    sys.path.append(_HERE)
from facts_schema import STATES, classify_expiry_class, is_noise_text, validate_fact  # noqa: E402
import quote_gate  # noqa: E402

# The redactor sits next to this file. REDACT_SECRETS_PATH overrides it.
_LIVE_REDACTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "redact_secrets.py")


def _scrubber():
    """The redactor's scrub_fact_text, loaded by file path from REDACT_SECRETS_PATH, else from the redactor
    next to this file. Raises if it cannot load, so ingest refuses rather than storing secrets unredacted."""
    import importlib.util  # noqa: PLC0415
    path = os.environ.get("REDACT_SECRETS_PATH") or _LIVE_REDACTOR
    spec = importlib.util.spec_from_file_location("redact_secrets_live", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"redactor not loadable: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.scrub_fact_text

# The data dir is orchestra_cli's data_dir() ($ORCHESTRA_DIR first), like every other script here.
# orchestra_cli lives in this file's checkout; appended, never prepended, so nothing is shadowed.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)
from orchestra_cli.settings import data_dir as _data_dir  # noqa: E402

ORCH = str(_data_dir())
DEFAULT_DB = os.path.join(ORCH, "state", "brain", "facts.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    fact_id      TEXT PRIMARY KEY,
    ts           TEXT NOT NULL,
    kind         TEXT NOT NULL,
    text         TEXT NOT NULL,
    actor        TEXT NOT NULL,
    confidence   REAL NOT NULL,
    entities     TEXT NOT NULL,   -- JSON
    lanes        TEXT NOT NULL,   -- JSON array
    provenance   TEXT NOT NULL,   -- JSON
    state        TEXT NOT NULL,
    expiry_class TEXT NOT NULL,
    superseded_by TEXT,          -- nullable fact_id of the fact that demotes this one
    ingested_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_ts ON facts(ts);
CREATE INDEX IF NOT EXISTS idx_facts_kind ON facts(kind);
-- (expiry_class, ts) lets facts_recall's tier-aware pass seek the few
-- durable/no-decay rows newest-first without a full-table scan (budget wall).
CREATE INDEX IF NOT EXISTS idx_facts_expiry_ts ON facts(expiry_class, ts);
CREATE TABLE IF NOT EXISTS ingest_meta (
    source_kind          TEXT PRIMARY KEY,
    last_source_event_ts TEXT,
    last_ingest_at       TEXT NOT NULL
);
"""


class BusLockTimeout(RuntimeError):
    """The bus write lock could not be acquired in time."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def lock_path_for(db_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "facts.lock")


@contextmanager
def bus_lock(db_path: str, timeout_s: float = 30.0):
    """Exclusive flock on the bus's own lockfile (registry_lock pattern)."""
    path = lock_path_for(db_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "a+")
    deadline = time.time() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    raise BusLockTimeout(
                        f"could not acquire {path} within {timeout_s}s — "
                        "another writer holds the bus lock")
                time.sleep(0.1)
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _migrate(con: sqlite3.Connection) -> None:
    """Additive column migrations for a pre-existing facts table. Idempotent —
    ALTER only when the column is absent (sqlite has no ADD COLUMN IF NOT EXISTS)."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(facts)")}
    if cols and "superseded_by" not in cols:
        con.execute("ALTER TABLE facts ADD COLUMN superseded_by TEXT")
    # ensure the tier index exists on pre-existing dbs (perf, budget wall)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_facts_expiry_ts ON facts(expiry_class, ts)")


def init_db(db_path: str = DEFAULT_DB) -> None:
    with bus_lock(db_path):
        con = _connect(db_path)
        try:
            con.executescript(_SCHEMA)
            _migrate(con)
            con.commit()
        finally:
            con.close()


def ingest(db_path: str, facts: list, *, source_kind: str,
           last_source_event_ts: str | None,
           lock_timeout_s: float = 30.0) -> dict:
    """Idempotent ingest: INSERT OR IGNORE keyed on fact_id (provenance hash).
    Validates every fact BEFORE taking the lock; records ingest_meta."""
    for f in facts:
        errs = validate_fact(f)
        if errs:
            raise ValueError(f"refusing ingest, invalid fact "
                             f"{f.get('fact_id')!r}: " + "; ".join(errs))
    if quote_gate.enabled():                     # label only: may set state='unverified', never drops a fact
        facts = quote_gate.label(facts)
    scrub = _scrubber()                          # before the lock: no redactor, no write
    inserted = 0
    with bus_lock(db_path, timeout_s=lock_timeout_s):
        con = _connect(db_path)
        try:
            con.executescript(_SCHEMA)
            _migrate(con)
            now = _now_iso()
            for f in facts:
                if is_noise_text(f["text"]):     # write-side noise filter
                    continue                     # never persist prompt-echoes
                cur = con.execute(
                    "INSERT OR IGNORE INTO facts (fact_id, ts, kind, text, "
                    "actor, confidence, entities, lanes, provenance, state, "
                    "expiry_class, superseded_by, ingested_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f["fact_id"], f["ts"], f["kind"], scrub(f["text"]), f["actor"],
                     f["confidence"], json.dumps(f["entities"]),
                     json.dumps(f["lanes"]), json.dumps(f["provenance"]),
                     f["state"], f["expiry_class"], f.get("superseded_by"), now))
                inserted += cur.rowcount
            con.execute(
                "INSERT INTO ingest_meta (source_kind, last_source_event_ts, "
                "last_ingest_at) VALUES (?,?,?) ON CONFLICT(source_kind) DO "
                "UPDATE SET last_source_event_ts=excluded.last_source_event_ts, "
                "last_ingest_at=excluded.last_ingest_at",
                (source_kind, last_source_event_ts, now))
            con.commit()
        finally:
            con.close()
    return {"inserted": inserted, "skipped": len(facts) - inserted}


def backfill_expiry_class(db_path: str = DEFAULT_DB,
                          lock_timeout_s: float = 30.0) -> int:
    """Recompute expiry_class for every row via the deterministic heuristic and
    write back the ones that change. Idempotent; returns rows updated.

    SINGLE-WRITER: runs under the bus write lock, so it serializes
    against the daily ingest cron — never a concurrent writer on facts.db.
    """
    updated = 0
    with bus_lock(db_path, timeout_s=lock_timeout_s):
        con = _connect(db_path)
        try:
            con.executescript(_SCHEMA)
            _migrate(con)
            rows = con.execute(
                "SELECT fact_id, kind, provenance, expiry_class FROM facts"
            ).fetchall()
            changes = []
            for fact_id, kind, prov_json, cur_ec in rows:
                try:
                    prov = json.loads(prov_json) if prov_json else {}
                except ValueError:
                    prov = {}
                want = classify_expiry_class(kind, prov)
                if want != cur_ec:
                    changes.append((want, fact_id))
            con.executemany(
                "UPDATE facts SET expiry_class=? WHERE fact_id=?", changes)
            con.commit()
            updated = len(changes)
        finally:
            con.close()
    return updated


def ingest_meta(db_path: str = DEFAULT_DB) -> dict:
    con = _connect(db_path)
    try:
        con.executescript(_SCHEMA)
        return {row[0]: {"last_source_event_ts": row[1],
                         "last_ingest_at": row[2]}
                for row in con.execute(
                    "SELECT source_kind, last_source_event_ts, last_ingest_at "
                    "FROM ingest_meta")}
    finally:
        con.close()


def update_state(db_path: str, fact_id: str, state: str,
                 lock_timeout_s: float = 30.0) -> None:
    """Append-safe lifecycle change: flips state only, never deletes."""
    if state not in STATES:
        raise ValueError(f"invalid state {state!r}; must be one of {sorted(STATES)}")
    with bus_lock(db_path, timeout_s=lock_timeout_s):
        con = _connect(db_path)
        try:
            con.execute("UPDATE facts SET state=? WHERE fact_id=?",
                        (state, fact_id))
            con.commit()
        finally:
            con.close()


def mark_superseded(db_path: str, old_fact_id: str, new_fact_id: str,
                    lock_timeout_s: float = 30.0) -> None:
    """Demote, never delete: flip old fact to state='superseded' and record the
    fact that replaced it. The row is retained (auditable), just down-ranked."""
    with bus_lock(db_path, timeout_s=lock_timeout_s):
        con = _connect(db_path)
        try:
            con.executescript(_SCHEMA)
            _migrate(con)
            con.execute(
                "UPDATE facts SET state='superseded', superseded_by=? "
                "WHERE fact_id=? AND state='active'",
                (new_fact_id, old_fact_id))
            con.commit()
        finally:
            con.close()


def query(db_path: str = DEFAULT_DB, *, kinds: list | None = None,
          since: str | None = None, limit: int | None = None) -> list:
    """Read-only fact query (no lock needed — WAL readers don't block)."""
    sql = ("SELECT fact_id, ts, kind, text, actor, confidence, entities, "
           "lanes, provenance, state, expiry_class, superseded_by, "
           "ingested_at FROM facts")
    where, params = [], []
    if kinds:
        where.append("kind IN (%s)" % ",".join("?" * len(kinds)))
        params.extend(kinds)
    if since:
        where.append("ts >= ?")
        params.append(since)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    con = _connect(db_path)
    try:
        con.executescript(_SCHEMA)
        _migrate(con)
        out = []
        for r in con.execute(sql, params):
            out.append({
                "fact_id": r[0], "ts": r[1], "kind": r[2], "text": r[3],
                "actor": r[4], "confidence": r[5],
                "entities": json.loads(r[6]), "lanes": json.loads(r[7]),
                "provenance": json.loads(r[8]), "state": r[9],
                "expiry_class": r[10], "superseded_by": r[11],
                "ingested_at": r[12]})
        return out
    finally:
        con.close()
