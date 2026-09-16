#!/usr/bin/env python3
"""Continuity v4 — write_fence SHADOW guard (Phase-0).

gm shadow-wiring gate 2026-08-21. This is OBSERVATION ONLY:
  - `observe()` ALWAYS accepts (returns None) and NEVER blocks or alters a real
    write. Authority writers call it immediately BEFORE their atomic write, then
    proceed exactly as today.
  - It logs the DECISION it WOULD make to the additive `cv4_write_fence_shadow`
    table: would_accept (stamped) / would_grandfather (unstamped legacy) /
    would_reject (a stamped-regime record arriving unstamped = downgrade).
  - Unstamped legacy writes are would-GRANDFATHER (logged, NOT rejected).
  - A raise anywhere inside the guard must NEVER break a real write: everything
    is wrapped; observe() cannot propagate.
  - Two independent off-switches: MODE flag (default 'shadow') and the
    CV4_WRITE_FENCE_DISABLED kill-switch. Enforcement (Phase-2) is a separate
    gm gate; nothing here ever rejects.
"""
from __future__ import annotations

import os
import sqlite3
import sys as _sys
from datetime import datetime, timezone
from pathlib import Path

_scripts_dbc = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _scripts_dbc not in _sys.path:
    _sys.path.insert(0, _scripts_dbc)
import db_connect  # B1-thin: shared tasks.db connect (WAL + 30s busy_timeout)

ORCHESTRA_DIR = Path(os.environ.get(
    "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
DEFAULT_DB = str(ORCHESTRA_DIR / "state" / "tasks.db")
KILL_SWITCH = Path(os.path.expanduser("~/runtime/CV4_WRITE_FENCE_DISABLED"))
MODE = "shadow"   # {shadow, enforce}; Phase-0 is shadow-only, never enforce

_SHADOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS cv4_write_fence_shadow (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    store     TEXT NOT NULL,
    key       TEXT,
    writer    TEXT,
    decision  TEXT NOT NULL,
    reason    TEXT,
    has_stamp INTEGER NOT NULL DEFAULT 0,
    cycle_id  TEXT
);
CREATE TABLE IF NOT EXISTS cv4_write_fence_cycle (
    cycle_id     TEXT,
    expected     INTEGER,
    classified   INTEGER,
    inserted     INTEGER,
    error        TEXT,
    completed_at TEXT
);
"""


def ensure_shadow_schema(db_path: str = DEFAULT_DB) -> None:
    con = db_connect.connect(db_path)
    try:
        con.executescript(_SHADOW_SCHEMA)
        con.commit()
    finally:
        con.close()


def _kill_active() -> bool:
    return KILL_SWITCH.exists()


def _effective_mode() -> str:
    """The live mode off-switch. A CLI writer (W4/W5) sets the env var, not the
    module attribute — so the env is the authoritative seam, with the module MODE
    constant as the default. (W7's cron gates the same env in its shell wrapper.)"""
    return os.environ.get("CV4_WRITE_FENCE_MODE", MODE)


def _ever_stamped(db_path: str, store: str, key) -> bool:
    """Anti-downgrade signal: has this (store,key) ever entered the stamped
    regime? Read from the append-only regime ledger if present; absent => no."""
    try:
        con = db_connect.connect(db_path)
        try:
            row = con.execute(
                "SELECT 1 FROM cv4_fence_regime WHERE store=? AND key=? LIMIT 1",
                (store, str(key))).fetchone()
            return row is not None
        finally:
            con.close()
    except sqlite3.Error:
        return False   # ledger table not created yet -> nothing is in-regime


# W7 state-snapshot projection: the ONLY keys a projection write may touch.
PROJECTION_TELEMETRY_KEYS = frozenset(
    {"last_snapshot_at", "was_running_at_snapshot", "last_seen_running"})


def classify_projection(on_disk, record, prior_unreadable=False):
    """Field-allowlist classifier for the W7 state-snapshot projection.

    A projection write may only ADD/UPDATE the three telemetry keys; touching
    any other key (a non-telemetry field, `_fence`, or an identity field) is an
    allowlist violation. `on_disk` is the prior record ({} if none), `record`
    is the record about to be written. `prior_unreadable=True` means the existing
    state file could not be parsed — we cannot prove telemetry-only, so it is a
    would_reject (the snapshot still writes; only the shadow judgement is honest).
    Returns (decision, reason)."""
    if prior_unreadable:
        return "would_reject", "malformed_prior_state"
    on_disk = on_disk if isinstance(on_disk, dict) else {}
    record = record if isinstance(record, dict) else {}
    changed = {k for k in set(on_disk) | set(record)
               if on_disk.get(k) != record.get(k)}
    violations = sorted(changed - PROJECTION_TELEMETRY_KEYS)
    if violations:
        return "would_reject", "allowlist_violation:" + ",".join(violations)
    return "would_accept", "projection_telemetry_only"


def record_projection_cycle(cycle_id, expected, rows, db: str = DEFAULT_DB,
                            error=None, timeout: float = 2.0) -> int:
    """Record a whole snapshot cycle in ONE connection / transaction: the
    per-agent decision rows (each tagged with `cycle_id`) AND one cv4_write_fence_
    cycle summary (cycle_id, expected, classified, inserted, error, completed_at)
    — so the >=3-cycle gate reads deterministic COMPLETED records, not fuzzy
    timestamp windows. A COMPLETED cycle row exists ONLY if the whole txn
    committed, so inserted == classified whenever a row is present.

    Returns the number of decision rows inserted (0 if the DB write failed — the
    caller MUST compare this to len(rows) and surface a visible error). A SHORT
    sqlite `timeout` keeps a locked DB from materially delaying the snapshot.
    Never raises: a broken shadow write must not break the */10 snapshot."""
    rows = list(rows)
    try:
        con = db_connect.connect(db, timeout=timeout)
        try:
            con.executescript(_SHADOW_SCHEMA)
            ts = datetime.now(timezone.utc).isoformat()
            if rows:
                con.executemany(
                    "INSERT INTO cv4_write_fence_shadow "
                    "(ts, store, key, writer, decision, reason, has_stamp, cycle_id) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    [(ts, store, None if key is None else str(key), writer,
                      decision, reason, 1 if has_stamp else 0, cycle_id)
                     for (store, key, writer, decision, reason, has_stamp) in rows])
            con.execute(
                "INSERT INTO cv4_write_fence_cycle "
                "(cycle_id, expected, classified, inserted, error, completed_at) "
                "VALUES (?,?,?,?,?,?)",
                (cycle_id, expected, len(rows), len(rows), error, ts))
            con.commit()
            return len(rows)
        finally:
            con.close()
    except Exception:
        return 0   # swallowed: shadow logging must never break the real write


def observe_projection(*, store: str, key=None, on_disk=None, record=None,
                       writer: str | None = None, db: str = DEFAULT_DB):
    """SHADOW observation for the W7 projection writer. ALWAYS returns None;
    logs the field-allowlist decision; NEVER blocks/alters/raises into the
    real snapshot write (a broken guard must not break the */10 snapshot)."""
    try:
        if MODE not in ("shadow", "enforce") or _kill_active():
            return None
        decision, reason = classify_projection(on_disk, record)
        has_stamp = isinstance(record, dict) and bool(record.get("_fence"))
        _log(db, store, key, writer, decision, reason, has_stamp)
    except Exception:
        try:
            _log(db, store, key, writer, "observe_error", "guard_exception", False)
        except Exception:
            pass   # a broken guard must NEVER break the real write
    return None


def _classify(record, on_disk, db_path, store, key):
    stamp = record.get("_fence") if isinstance(record, dict) else None
    if stamp:
        return "would_accept", "stamped"
    if _ever_stamped(db_path, store, key):
        return "would_reject", "missing_stamp_on_stamped_record (downgrade)"
    return "would_grandfather", "unstamped_legacy_writer"


def _log(db_path, store, key, writer, decision, reason, has_stamp) -> None:
    ensure_shadow_schema(db_path)
    con = db_connect.connect(db_path)
    try:
        con.execute(
            "INSERT INTO cv4_write_fence_shadow "
            "(ts, store, key, writer, decision, reason, has_stamp) "
            "VALUES (?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), store,
             None if key is None else str(key), writer, decision, reason,
             1 if has_stamp else 0))
        con.commit()
    finally:
        con.close()


def observe(*, store: str, key=None, record=None, on_disk=None,
            writer: str | None = None, db: str = DEFAULT_DB):
    """SHADOW observation. ALWAYS returns None (accept); NEVER blocks/raises.

    Call this immediately before an authority-store atomic write, then proceed
    exactly as today. Any internal failure is swallowed (best-effort logged);
    it must never break the real write."""
    try:
        if MODE not in ("shadow", "enforce") or _kill_active():
            return None
        decision, reason = _classify(record, on_disk, db, store, key)
        has_stamp = isinstance(record, dict) and bool(record.get("_fence"))
        _log(db, store, key, writer, decision, reason, has_stamp)
    except Exception:
        try:
            _log(db, store, key, writer, "observe_error", "guard_exception", False)
        except Exception:
            pass   # a broken guard must NEVER break a real write
    return None    # Phase-0: shadow always accepts


def observe_write(*, store: str, key=None, record=None, on_disk=None,
                  writer: str | None = None, db: str = DEFAULT_DB,
                  timeout: float = 2.0) -> int:
    """SHADOW-observe a SINGLE authority write (W4 sessions-update / W5
    registry-update). Classifies + logs the would-decision in ONE short-timeout
    connection and RETURNS a status the caller MUST check (gm gate: verify by the
    RETURN, never the absence of an exception — the W7 dead-except trap):
        1  = logged (would_accept / would_grandfather / would_reject)
        0  = shadow write FAILED (caller surfaces a visible error; real write proceeds)
       -1  = disabled (kill-switch or mode off — caller stays silent)
    NEVER raises: a broken guard must never break the real authority write."""
    try:
        if _effective_mode() not in ("shadow", "enforce") or _kill_active():
            return -1
        decision, reason = _classify(record, on_disk, db, store, key)
        has_stamp = 1 if isinstance(record, dict) and record.get("_fence") else 0
        con = db_connect.connect(db, timeout=timeout)
        try:
            con.executescript(_SHADOW_SCHEMA)
            con.execute(
                "INSERT INTO cv4_write_fence_shadow "
                "(ts, store, key, writer, decision, reason, has_stamp) "
                "VALUES (?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), store,
                 None if key is None else str(key), writer, decision, reason,
                 has_stamp))
            con.commit()
        finally:
            con.close()
        return 1
    except Exception:
        return 0   # visible-failure signal; the real write must still proceed


def shadow_counts(db: str = DEFAULT_DB) -> dict:
    try:
        con = db_connect.connect(db)
        try:
            return {d: n for d, n in con.execute(
                "SELECT decision, COUNT(*) FROM cv4_write_fence_shadow "
                "GROUP BY decision").fetchall()}
        finally:
            con.close()
    except sqlite3.Error:
        return {}


def recent(db: str = DEFAULT_DB, decision: str | None = None, limit: int = 20):
    try:
        con = db_connect.connect(db)
        con.row_factory = sqlite3.Row
        try:
            q = ("SELECT ts, store, key, writer, decision, reason FROM "
                 "cv4_write_fence_shadow")
            args = ()
            if decision:
                q += " WHERE decision=?"
                args = (decision,)
            q += " ORDER BY id DESC LIMIT ?"
            return [dict(r) for r in con.execute(q, args + (limit,)).fetchall()]
        finally:
            con.close()
    except sqlite3.Error:
        return []
