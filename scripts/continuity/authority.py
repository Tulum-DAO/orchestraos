#!/usr/bin/env python3
"""Continuity-v4 Authority Layer (DESIGN spec v2, DEC-1787319372 CONSENSUS_REACHED).

DURABLE, integration-strength authority state on sqlite (state/tasks.db). This is
the ENFORCEMENT PREREQUISITE — but it stays SHADOW: authorize_write is wired +
provable by effect, yet NO writer's enforcement is armed (a separate the operator gate).

Build order (§10): (e) tables -> (a) mint_epoch -> (b) lease+CAS -> (c) regime
ledger -> cross-store guard + write-intent + fail-closed reader + §4.4 recovery
-> (d) break-glass -> wire authorize_write.

This file currently implements: (e) durable schema.

Off the critical path by design: everything is additive `CREATE TABLE IF NOT
EXISTS`; no existing table is altered; no JSON store is touched here.
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys as _sys
_scripts_dbc = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _scripts_dbc not in _sys.path:
    _sys.path.insert(0, _scripts_dbc)
import db_connect  # B1-thin: shared tasks.db connect (WAL + 30s busy_timeout)

ORCHESTRA_DIR = Path(os.environ.get(
    "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
DEFAULT_DB = str(ORCHESTRA_DIR / "state" / "tasks.db")

MINT_REASONS = frozenset(
    {"rotation", "rollback", "recovery", "adoption", "repair"})


class WriteFenceRejected(RuntimeError):
    """Fail-CLOSED: an authority operation was refused (freeze, never silent)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(ts):
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None

# §7 spec v2 DDL. Additive; WAL already in use on tasks.db.
_SCHEMA = """
-- issuer-of-record for epochs (append-only; one row per mint)
CREATE TABLE IF NOT EXISTS cv4_seat_epochs (
    mint_id         TEXT PRIMARY KEY,
    seat            TEXT NOT NULL,
    epoch           INTEGER NOT NULL,
    reason          TEXT NOT NULL,
    authorized_by   TEXT NOT NULL,
    lease_token     TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    minted_at       TEXT NOT NULL,
    UNIQUE (seat, idempotency_key),
    UNIQUE (seat, epoch)
);

-- single-writer lease (uniqueness of a LIVE lease = the split-brain arbiter)
CREATE TABLE IF NOT EXISTS cv4_leases (
    token         TEXT PRIMARY KEY,
    seat          TEXT NOT NULL,
    holder_id     TEXT NOT NULL,
    authorized_by TEXT NOT NULL,
    granted_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    released_at   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS cv4_leases_one_live
    ON cv4_leases (seat) WHERE released_at IS NULL;

-- append-only regime ledger (anti-downgrade + rollback history)
CREATE TABLE IF NOT EXISTS cv4_fence_regime (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    seat    TEXT NOT NULL,
    store   TEXT,
    key     TEXT,
    epoch   INTEGER NOT NULL,
    mint_id TEXT NOT NULL,
    event   TEXT NOT NULL DEFAULT 'entered_regime',
    reverts TEXT,
    ts      TEXT NOT NULL
);

-- write-intent marker (in-repair detector AND F10 recovery anchor; cleared on commit)
CREATE TABLE IF NOT EXISTS cv4_write_intent (
    seat         TEXT PRIMARY KEY,
    target_epoch INTEGER NOT NULL,
    mint_id      TEXT NOT NULL,
    holder_id    TEXT NOT NULL,
    lease_token  TEXT NOT NULL,
    began_at     TEXT NOT NULL
);

-- authenticated break-glass (attributable enforcement-disable)
CREATE TABLE IF NOT EXISTS cv4_break_glass (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id      TEXT NOT NULL,
    reason        TEXT NOT NULL,
    authorized_by TEXT NOT NULL,
    opened_at     TEXT NOT NULL,
    closed_at     TEXT
);
"""


def ensure_authority_schema(db: str = DEFAULT_DB) -> None:
    """Create the 6 authority tables + the live-lease partial-unique index.
    Additive + idempotent; never alters an existing table."""
    con = db_connect.connect(db)
    try:
        con.executescript(_SCHEMA)
        con.commit()
    finally:
        con.close()


# ---- (a) EPOCH MINT — controller is the SOLE issuer (A2-2, F1) --------------

def mint_epoch(*, seat: str, reason: str, authorized_by: str,
               lease_token: str, idempotency_key: str,
               db: str = DEFAULT_DB, lease_valid=None,
               db_timeout_s: float = 5.0) -> dict:
    """Mint the next seat_epoch for `seat` — the ONE issuer path for ALL five
    reasons (rotation/rollback/recovery/adoption/repair; no side-door
    incrementer). Monotonic MAX(epoch)+1 computed INSIDE the write transaction;
    idempotent on (seat, idempotency_key) so a retry returns the SAME mint (F1:
    no double-increment). Fail-CLOSED (WriteFenceRejected) on any missing
    precondition or an unknown reason.

    `lease_valid(seat, token) -> bool` is the lease gate. Default (None) only
    requires a non-empty token (structural); component (b) injects the real
    lease-holder check (`lease_held`). A False verdict FREEZES.

    ⚠️ db-BIND FOOTGUN (verifier carry-forward): this calls `lease_valid(seat,
    token)` 2-arg and does NOT thread `db=` through. `lease_held`'s db defaults to
    DEFAULT_DB, so in prod (db==DEFAULT_DB) bare `lease_valid=lease_held` is
    correct — but any OFF-default-db caller (incl. scratch tests) MUST bind the db:
    `lease_valid=lambda s, t: lease_held(s, t, db=my_db)`. Otherwise the gate
    checks the wrong (default) database.

    Returns {seat, epoch, mint_id, reason, authorized_by, lease_token,
    idempotency_key, minted_at}.
    """
    if reason not in MINT_REASONS:
        raise WriteFenceRejected(
            f"mint_epoch FREEZE: unknown reason {reason!r} (allowed: "
            f"{sorted(MINT_REASONS)}) — no side-door mint.")
    if not (seat and str(seat).strip()):
        raise WriteFenceRejected("mint_epoch FREEZE: empty seat.")
    if not (lease_token and str(lease_token).strip()):
        raise WriteFenceRejected(
            "mint_epoch FREEZE: no lease_token — only the lease-holder mints.")
    if not (authorized_by and str(authorized_by).strip()):
        raise WriteFenceRejected(
            "mint_epoch FREEZE: no authorized_by — authorship is required.")
    if not (idempotency_key and str(idempotency_key).strip()):
        raise WriteFenceRejected(
            "mint_epoch FREEZE: no idempotency_key — idempotency is required.")
    if lease_valid is not None and not lease_valid(seat, lease_token):
        raise WriteFenceRejected(
            f"mint_epoch FREEZE: lease {lease_token!r} is not held for seat "
            f"{seat!r} — only the active lease-holder may mint.")

    con = db_connect.connect(db, timeout=db_timeout_s)
    try:
        con.isolation_level = None            # explicit txn control
        con.execute("BEGIN IMMEDIATE")        # serialize concurrent minters
        # idempotency: a prior mint with this key returns the SAME mint.
        prior = con.execute(
            "SELECT mint_id, epoch, reason, authorized_by, lease_token, "
            "minted_at FROM cv4_seat_epochs WHERE seat=? AND idempotency_key=?",
            (seat, idempotency_key)).fetchone()
        if prior:
            con.execute("COMMIT")
            return {"seat": seat, "epoch": prior[1], "mint_id": prior[0],
                    "reason": prior[2], "authorized_by": prior[3],
                    "lease_token": prior[4], "idempotency_key": idempotency_key,
                    "minted_at": prior[5]}
        # monotonic MAX+1 INSIDE the txn (BEGIN IMMEDIATE holds the write lock).
        row = con.execute(
            "SELECT MAX(epoch) FROM cv4_seat_epochs WHERE seat=?", (seat,)
        ).fetchone()
        epoch = (row[0] or 0) + 1
        mint_id = uuid.uuid4().hex
        minted_at = _now()
        con.execute(
            "INSERT INTO cv4_seat_epochs (mint_id, seat, epoch, reason, "
            "authorized_by, lease_token, idempotency_key, minted_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (mint_id, seat, epoch, reason, authorized_by, lease_token,
             idempotency_key, minted_at))
        con.execute("COMMIT")
        return {"seat": seat, "epoch": epoch, "mint_id": mint_id,
                "reason": reason, "authorized_by": authorized_by,
                "lease_token": lease_token, "idempotency_key": idempotency_key,
                "minted_at": minted_at}
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


# ---- (b) SINGLE-WRITER LEASE + CAS (F2 split-brain, F3 lost-update) ---------

def _reap_expired(con, seat, now_dt):
    """Mark an expired-but-unreleased lease for `seat` as released, freeing the
    partial-unique index so a fresh holder can acquire. Expiry != silent
    takeover: the reaped holder's token is dead; the new holder acquires afresh."""
    rows = con.execute(
        "SELECT token, expires_at FROM cv4_leases "
        "WHERE seat=? AND released_at IS NULL", (seat,)).fetchall()
    for token, expires_at in rows:
        exp = _parse(expires_at)
        if exp is not None and exp <= now_dt:
            con.execute("UPDATE cv4_leases SET released_at=? WHERE token=?",
                        (now_dt.isoformat(), token))


def acquire_lease(*, seat: str, holder_id: str, ttl_s: int,
                  authorized_by: str, db: str = DEFAULT_DB,
                  db_timeout_s: float = 5.0) -> dict:
    """Acquire the single-writer lease for `seat`. Succeeds ONLY if no LIVE lease
    exists (the cv4_leases_one_live partial-unique index is the arbiter — F2:
    split-brain is impossible). An expired-but-unreleased lease is reaped first.
    Fail-CLOSED (WriteFenceRejected) if the seat is already live-leased or on a
    missing precondition. Never raises IntegrityError to the caller (that becomes
    a FREEZE)."""
    if not (seat and str(seat).strip()):
        raise WriteFenceRejected("acquire_lease FREEZE: empty seat.")
    if not (holder_id and str(holder_id).strip()):
        raise WriteFenceRejected("acquire_lease FREEZE: empty holder_id.")
    if not (authorized_by and str(authorized_by).strip()):
        raise WriteFenceRejected("acquire_lease FREEZE: no authorized_by.")
    now_dt = datetime.now(timezone.utc)
    token = uuid.uuid4().hex
    expires_at = (now_dt + timedelta(seconds=int(ttl_s))).isoformat()
    con = db_connect.connect(db, timeout=db_timeout_s)
    try:
        con.isolation_level = None
        con.execute("BEGIN IMMEDIATE")
        _reap_expired(con, seat, now_dt)
        try:
            con.execute(
                "INSERT INTO cv4_leases (token, seat, holder_id, authorized_by, "
                "granted_at, expires_at, released_at) VALUES (?,?,?,?,?,?,NULL)",
                (token, seat, holder_id, authorized_by, now_dt.isoformat(),
                 expires_at))
        except sqlite3.IntegrityError:
            con.execute("ROLLBACK")
            raise WriteFenceRejected(
                f"acquire_lease FREEZE: seat {seat!r} already has a LIVE lease "
                f"(single-writer). Wait for release/expiry.")
        con.execute("COMMIT")
        return {"token": token, "seat": seat, "holder_id": holder_id,
                "authorized_by": authorized_by, "granted_at": now_dt.isoformat(),
                "expires_at": expires_at}
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


def release_lease(*, seat: str, token: str, db: str = DEFAULT_DB,
                  db_timeout_s: float = 5.0) -> None:
    con = db_connect.connect(db, timeout=db_timeout_s)
    try:
        con.execute(
            "UPDATE cv4_leases SET released_at=? WHERE seat=? AND token=? "
            "AND released_at IS NULL", (_now(), seat, token))
        con.commit()
    finally:
        con.close()


def renew_lease(*, seat: str, token: str, ttl_s: int,
                db: str = DEFAULT_DB) -> None:
    """Extend a LIVE, un-expired lease. A renew on an expired/released token is a
    no-op (the holder must re-acquire) — expiry is never a silent takeover."""
    now_dt = datetime.now(timezone.utc)
    new_exp = (now_dt + timedelta(seconds=int(ttl_s))).isoformat()
    con = db_connect.connect(db, timeout=5.0)
    try:
        con.execute(
            "UPDATE cv4_leases SET expires_at=? WHERE seat=? AND token=? "
            "AND released_at IS NULL AND expires_at > ?",
            (new_exp, seat, token, now_dt.isoformat()))
        con.commit()
    finally:
        con.close()


def lease_held(seat: str, token: str, db: str = DEFAULT_DB, now=None,
               db_timeout_s: float = 5.0) -> bool:
    """The real lease_valid hook for mint_epoch / authorize_write: True iff
    `token` is the LIVE, un-expired, un-released lease for `seat`. NEVER consults
    a scanned last_active (A2-8) — liveness of the HOLDER is the caller's
    tmux-authoritative concern; this only validates the lease row itself."""
    now_dt = now or datetime.now(timezone.utc)
    con = db_connect.connect(db, timeout=db_timeout_s)
    try:
        row = con.execute(
            "SELECT expires_at FROM cv4_leases WHERE seat=? AND token=? "
            "AND released_at IS NULL", (seat, token)).fetchone()
    finally:
        con.close()
    if not row:
        return False
    exp = _parse(row[0])
    return exp is not None and exp > now_dt


def get_or_acquire_lease(*, seat: str, holder_id: str, ttl_s: int = 86400,
                         authorized_by: str = "adapter", db: str = DEFAULT_DB) -> str | None:
    """Return the currently live lease token for `seat`, or acquire a fresh one."""
    now_dt = datetime.now(timezone.utc)
    con = db_connect.connect(db, timeout=5.0)
    try:
        row = con.execute(
            "SELECT token, expires_at FROM cv4_leases WHERE seat=? "
            "AND released_at IS NULL AND expires_at > ? ORDER BY granted_at DESC LIMIT 1",
            (seat, now_dt.isoformat())).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    finally:
        con.close()

    try:
        res = acquire_lease(seat=seat, holder_id=holder_id, ttl_s=ttl_s,
                            authorized_by=authorized_by, db=db)
        return res.get("token")
    except Exception:
        return None


def cas_write(*, record: dict, expected_version: int) -> dict:
    """Optimistic-concurrency compare-and-swap on a record's
    `_fence.authority_version` (F3). The caller asserts "I derived from version
    `expected_version`"; if the record's on-disk version differs, a lost-update
    happened -> FREEZE. On success returns a COPY with the version bumped to
    expected+1. An unstamped record is treated as version 0.

    ⚠️ LOAD-BEARING (independent-verifier carry-forward, DEC-1787319372): this is a
    PURE STATELESS COMPARATOR — it does NOT touch disk or serialize. F3 is ONLY
    closed when the CALLER wraps this in BEGIN IMMEDIATE / LOCK_EX AND RE-READS the
    on-disk `authority_version` immediately before calling. Two concurrent callers
    holding the same in-memory N will BOTH bump to N+1 if unwrapped. The receipt-
    write path (cross-store guard / authorize_write leg) MUST provide the
    lock+re-read, with an explicit test that two concurrent RECEIPT writes cannot
    both land. Do not call cas_write outside a serialized re-read boundary."""
    rec = dict(record) if isinstance(record, dict) else {}
    fence = dict(rec.get("_fence") or {})
    on_disk_version = fence.get("authority_version", 0)
    if on_disk_version != expected_version:
        raise WriteFenceRejected(
            f"cas_write FREEZE: authority_version lost-update — derived from "
            f"{expected_version}, on-disk is {on_disk_version}. Re-read + retry.")
    fence["authority_version"] = expected_version + 1
    rec["_fence"] = fence
    return rec


# ---- (c) APPEND-ONLY REGIME LEDGER (F4 anti-downgrade, T7 rollback) ---------

def enter_regime(*, seat: str, store: str, key: str, epoch: int,
                 mint_id: str, db: str = DEFAULT_DB) -> None:
    """Append a 'seat entered stamped-regime' row to cv4_fence_regime. Append-only:
    never mutated/deleted. Once a (seat[,store,key]) is here, a later missing/blank
    `_fence` on its record is a HARD downgrade reject (F4) — membership is read from
    THIS ledger, never from whether the mutable record still carries `_fence`."""
    con = db_connect.connect(db, timeout=5.0)
    try:
        con.execute(
            "INSERT INTO cv4_fence_regime (seat, store, key, epoch, mint_id, "
            "event, reverts, ts) VALUES (?,?,?,?,?, 'entered_regime', NULL, ?)",
            (seat, store, key, epoch, mint_id, _now()))
        con.commit()
    finally:
        con.close()


def is_in_regime(seat: str, store: str = None, key: str = None,
                 db: str = DEFAULT_DB) -> bool:
    """True iff `seat` (optionally scoped to store+key) has EVER entered the
    stamped regime per the append-only ledger. A 'rolled_back' event does not
    remove membership (rollback is an explicit transition, not an erasure — the
    seat was in-regime; T7)."""
    q = ("SELECT 1 FROM cv4_fence_regime WHERE seat=? "
         "AND event='entered_regime'")
    args = [seat]
    if store is not None:
        q += " AND store=?"
        args.append(store)
    if key is not None:
        q += " AND key=?"
        args.append(key)
    q += " LIMIT 1"
    con = db_connect.connect(db, timeout=5.0)
    try:
        return con.execute(q, tuple(args)).fetchone() is not None
    finally:
        con.close()


def _has_stamp(record) -> bool:
    return isinstance(record, dict) and bool(record.get("_fence"))


def reject_if_downgrade(*, seat: str, store: str, key: str, record,
                        db: str = DEFAULT_DB) -> None:
    """F4: if (seat,store,key) is in the regime ledger AND `record` arrives with a
    missing/blank `_fence`, FREEZE (WriteFenceRejected) — a downgrade. If not yet
    ledgered, an unstamped write is grandfathered (no raise). Reads membership from
    the LEDGER, so stripping `_fence` from the live record cannot erase the
    evidence (the downgrade attack)."""
    if _has_stamp(record):
        return
    if is_in_regime(seat, store=store, key=key, db=db):
        raise WriteFenceRejected(
            f"reject_if_downgrade FREEZE: {store}/{key} (seat {seat!r}) is in the "
            f"stamped regime (per the append-only ledger) but this write carries "
            f"NO _fence — a downgrade. Stamp the write with the current epoch or "
            f"resolve the regression by hand. NOTHING accepted.")
    # not ledgered -> grandfathered (still accept-all in shadow)


def record_rollback(*, seat: str, epoch: int, mint_id: str, reverts: str,
                    db: str = DEFAULT_DB) -> None:
    """T7: append an explicit 'rolled_back' event (reverts the named mint_id). The
    prior entered_regime row is PRESERVED (append-only, never erased) — a rollback
    is a durable transition in the rotation history, not a deletion."""
    con = db_connect.connect(db, timeout=5.0)
    try:
        con.execute(
            "INSERT INTO cv4_fence_regime (seat, store, key, epoch, mint_id, "
            "event, reverts, ts) VALUES (?, NULL, NULL, ?, ?, 'rolled_back', ?, ?)",
            (seat, epoch, mint_id, reverts, _now()))
        con.commit()
    finally:
        con.close()


# ---- F6 PART 1: cross-store guard + write-intent + fail-closed reader -------
# (F10 in-repair RECOVERY is PART 2 — a separate leg.)

_STORES = ("registry", "sessions", "state/agents")


def _epoch_of(record):
    if isinstance(record, dict):
        f = record.get("_fence")
        if isinstance(f, dict):
            return f.get("seat_epoch")
    return None


def _version_of(record):
    if isinstance(record, dict):
        f = record.get("_fence")
        if isinstance(f, dict):
            return f.get("authority_version")
    return None


def _skew(records):
    """Return a reason string if the 3 stores are NOT coherent, else None.
    Coherent = ALL THREE stores present AND carrying the SAME (seat_epoch,
    authority_version) — or all-unstamped ((None,None) across all three =
    grandfathered legacy). Fails CLOSED on malformed input.

    SEAM #1 (verifier fold, spec §4.3 'epoch/VERSION skew' + P3): compare the FULL
    (epoch, version) tuple, not just epoch — a same-epoch/differing-version state
    is a half-applied CAS bump (a partial write) and MUST read as skew. Reachable
    the moment PART 2 wires per-store authority_version CAS.
    SEAM #2 (verifier fold): a MISSING store slot is treated as skew (absent =>
    (None,None) which won't match a stamped peer), so a malformed caller passing
    only 1-2 of the 3 stores fails CLOSED (in-repair), never open. All 3 store
    keys must be present."""
    if records is None:
        return "no records provided (all 3 stores absent)"
    sig = {s: (_epoch_of(records.get(s)), _version_of(records.get(s)))
           for s in _STORES}          # ALL 3 stores, absent => (None, None)
    vals = set(sig.values())
    if len(vals) <= 1:
        return None                   # all three identical (incl. all-unstamped)
    return f"epoch/version skew across stores: {sig}"


def assert_stores_coherent(*, seat: str, records: dict) -> None:
    """Guard-all-3-then-write (§4.3): pre-write, assert the 3 store records are
    coherent (same seat_epoch, or all unstamped). FREEZE on skew. Detectability
    alone is not enough — this guard PLUS the fail-closed reader (seat_in_repair)
    is what makes the non-atomic 3-file write acceptable transitionally."""
    reason = _skew(records)
    if reason is not None:
        raise WriteFenceRejected(
            f"assert_stores_coherent FREEZE: seat {seat!r} — {reason}. Refusing "
            f"to write onto an incoherent 3-store state.")


def begin_write_intent(*, seat: str, target_epoch: int, mint_id: str,
                       holder_id: str, lease_token: str,
                       db: str = DEFAULT_DB) -> None:
    """Insert the durable write-intent marker BEFORE the 3-store write (§4.3). One
    open intent per seat (seat PRIMARY KEY) — a second begin without a clear
    FREEZES (never two open intents). Carries holder_id + lease_token so PART 2
    recovery is scoped to the lease (F10)."""
    con = db_connect.connect(db, timeout=5.0)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(
                "INSERT INTO cv4_write_intent (seat, target_epoch, mint_id, "
                "holder_id, lease_token, began_at) VALUES (?,?,?,?,?,?)",
                (seat, target_epoch, mint_id, holder_id, lease_token, _now()))
        except sqlite3.IntegrityError:
            con.execute("ROLLBACK")
            raise WriteFenceRejected(
                f"begin_write_intent FREEZE: seat {seat!r} already has an OPEN "
                f"write-intent (in-repair). Resolve it (PART-2 recovery) before a "
                f"new rotation write.")
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


def open_write_intent(seat: str, db: str = DEFAULT_DB):
    con = db_connect.connect(db, timeout=5.0)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT seat, target_epoch, mint_id, holder_id, lease_token, "
            "began_at FROM cv4_write_intent WHERE seat=?", (seat,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def clear_write_intent(*, seat: str, lease_token: str,
                       db: str = DEFAULT_DB) -> None:
    """Clear the write-intent on commit — ONLY under the matching lease_token (the
    holder that opened it, or the PART-2 recoverer holding a fresh lease). A
    non-matching token does NOT clear (prevents an unrelated writer erasing an
    in-repair marker)."""
    con = db_connect.connect(db, timeout=5.0)
    try:
        con.execute(
            "DELETE FROM cv4_write_intent WHERE seat=? AND lease_token=?",
            (seat, lease_token))
        con.commit()
    finally:
        con.close()


def seat_in_repair(*, seat: str, records: dict, db: str = DEFAULT_DB):
    """Fail-closed reader (§4.3): a seat is in-repair / do-not-trust if a LIVE
    write-intent exists OR the 3 stores show epoch/version skew. Returns
    (in_repair: bool, reason: str)."""
    if open_write_intent(seat, db=db) is not None:
        return True, "a live write-intent marker exists (rotation in progress)"
    reason = _skew(records)
    if reason is not None:
        return True, f"cross-store {reason}"
    return False, "coherent, no open intent"


def read_trusted_seat(*, seat: str, records: dict, db: str = DEFAULT_DB):
    """The F6 core guarantee: NEVER pick the newer record as winner while the seat
    is in-repair. Returns the coherent winning record when trustworthy, else None
    (do-not-trust — caller must treat the seat as in-repair, never select the
    higher-epoch half-write)."""
    in_repair, _ = seat_in_repair(seat=seat, records=records, db=db)
    if in_repair:
        return None
    for s in _STORES:
        if s in records:
            return records[s]
    return None


# ---- F6 PART 2: §4.4 in-repair RECOVERY (F10) + serialized receipt write ----

def write_receipt(*, path: str, key: str, mutate) -> dict:
    """Carry-forward #1: the SERIALIZED receipt-write boundary that makes cas_write
    (a pure comparator) actually close F3. Under an fcntl.LOCK_EX on the store
    file, RE-READ the on-disk authority_version, apply `mutate(record)`, then
    cas_write(expected=on_disk_version) and persist atomically. Two concurrent
    receipt writers therefore serialize: writer A does N->N+1, writer B (blocked on
    the lock) re-reads N+1 and does N+1->N+2 — never both landing N->N+1 (a lost
    update). A stale CAS inside the lock FREEZES. Returns the written record."""
    with open(path, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            data = json.load(f)
            record = data.get(key) or {}
            on_disk_version = ((record.get("_fence") or {}).get(
                "authority_version", 0))
            record = mutate(dict(record))
            record = cas_write(record=record, expected_version=on_disk_version)
            data[key] = record
            f.seek(0)
            json.dump(data, f)
            f.truncate()
            f.flush()
            os.fsync(f.fileno())
            return record
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def recover_in_repair_seat(*, seat: str, new_holder: str, complete_receipts,
                           ttl_s: int, db: str = DEFAULT_DB, alert=None) -> dict:
    """§4.4 F10 in-repair recovery — makes containment OPERABLE (contain != freeze
    forever). A crashed lease-holder mid-3-store-write left an open write-intent +
    partial receipts. This:
      1. no open intent -> nothing to recover (idempotent no-op).
      2. FREEZE if the stranded lease is still LIVE (can't recover a live seat;
         wait for expiry — a briefly-resurrected dead holder can't race us).
      3. acquire a FRESH lease for `new_holder` (never reuse the stranded token;
         acquire reaps the expired stranded lease first).
      4. epoch: if the intent's mint row EXISTS -> COMPLETE receipts at that
         EXISTING target_epoch/mint_id (no re-mint). If the mint is ABSENT (crash
         before the durable mint) -> mint a fresh reason=repair epoch and complete
         at it. Either way exactly ONE mint for the transition (no double-mint).
      5. `complete_receipts(seat, epoch, mint_id)` re-drives the 3-store receipt
         writes (via write_receipt, serialized).
      6. clear the intent UNDER THE NEW LEASE.
      7. stuck-in-repair (began_at older than 2x ttl) -> raise the SAME
         blocked-feedback alert as break-glass (Telegram/approvals) via `alert`.
    Returns {recovered, reason?, epoch?, mint_id?, minted_repair?, lease_token?}.

    ⚠️ LOAD-BEARING CONTRACT — `complete_receipts` MUST BE IDEMPOTENT / LAGGARD-ONLY
    (harness FINDING #1, crash-harness B7/B7b; gm RULING 2026-08-21 = carry-forward,
    keystone-phase). This function does NOT write receipts itself — it DELEGATES to
    the caller-supplied `complete_receipts`, and does NOT enforce idempotency. The
    completer MUST advance ONLY the stores whose on-disk state is BELOW the target
    (seat_epoch != target); it MUST NOT re-bump a store the crashed holder already
    advanced. `write_receipt` bumps `authority_version` on EVERY call, and cross-store
    coherence is the FULL (seat_epoch, authority_version) tuple (the _skew SEAM#1
    fold), so a naive "rewrite all 3 unconditionally" completer re-bumps the already-
    advanced store (e.g. registry v1->v2) while laggards go v0->v1, leaving residual
    VERSION skew (6,2)/(6,1)/(6,1). That is FAIL-CLOSED (safe — never a false trust)
    but NOT OPERABLE: SEAM#1 then holds the seat in-repair FOREVER ('contain != freeze
    forever', §4.4 liveness violation). A laggard-only completer converges all 3 to
    the same version -> coherent -> trusted. The keystone `promote_successor` completer
    MUST satisfy this contract. (Crash-harness B7 proves convergence with an idempotent
    completer; B7b pins the naive-completer trap as a strict-xfail.)"""
    intent = open_write_intent(seat, db=db)
    if intent is None:
        return {"recovered": False, "reason": "no open write-intent for seat"}

    # stuck-in-repair alert (before we heal it — so the stuck condition is recorded)
    began = _parse(intent["began_at"])
    if began is not None and alert is not None:
        age = (datetime.now(timezone.utc) - began).total_seconds()
        if age > 2 * int(ttl_s):
            alert(seat=seat, stranded_holder=intent["holder_id"],
                  stranded_token=intent["lease_token"],
                  target_epoch=intent["target_epoch"], age_s=age,
                  kind="cv4_stuck_in_repair")

    # FREEZE if the stranded lease is still live.
    if lease_held(seat, intent["lease_token"], db=db):
        raise WriteFenceRejected(
            f"recover_in_repair_seat FREEZE: seat {seat!r} stranded lease "
            f"{intent['lease_token']!r} is still LIVE — recovery may only proceed "
            f"after the crashed holder's lease EXPIRES.")

    # fresh lease for the recoverer (reaps the expired stranded lease).
    lease = acquire_lease(seat=seat, holder_id=new_holder, ttl_s=ttl_s,
                          authorized_by=f"repair:{intent['mint_id']}", db=db)

    # RE-READ the intent UNDER THE LEASE (concurrent-recovery fold, verifier
    # DEC-1787319372): the top-of-fn read is a TOCTOU — a peer recoverer may have
    # WON the lease, completed, cleared the intent, and released it BEFORE we
    # acquired the just-freed lease. Without this re-read, we would re-drive
    # complete_receipts + append a duplicate regime row with STALE intent state
    # (the loser must no-op, per spec §4.4 "single-writer lease makes repair
    # idempotent"). acquire_lease only succeeds after the winner RELEASES, and the
    # winner clears the intent BEFORE releasing, so a now-empty intent == already
    # recovered => release our lease and no-op.
    fresh = open_write_intent(seat, db=db)
    if fresh is None:
        release_lease(seat=seat, token=lease["token"], db=db)
        return {"recovered": False,
                "reason": "no open write-intent after lease acquire — a "
                          "concurrent recoverer already completed this seat"}
    intent = fresh   # use the lease-guarded read for the rest of the repair

    # epoch: complete at the existing mint, or repair-mint if it's absent.
    mint_present = db_connect.connect(db).execute(
        "SELECT 1 FROM cv4_seat_epochs WHERE mint_id=?",
        (intent["mint_id"],)).fetchone() is not None
    minted_repair = False
    if mint_present:
        epoch = intent["target_epoch"]
        mint_id = intent["mint_id"]
    else:
        m = mint_epoch(seat=seat, reason="repair",
                       authorized_by=f"repair:{intent['mint_id']}",
                       lease_token=lease["token"],
                       idempotency_key=f"repair-{intent['mint_id']}", db=db,
                       lease_valid=lambda s, t: lease_held(s, t, db=db))
        epoch = m["epoch"]
        mint_id = m["mint_id"]
        minted_repair = True

    # complete the partial receipts to the resolved epoch (serialized writes).
    complete_receipts(seat, epoch, mint_id)

    # clear the intent under the NEW lease, and ledger the completion.
    clear_write_intent(seat=seat, lease_token=intent["lease_token"], db=db)
    enter_regime(seat=seat, store=None, key=None, epoch=epoch, mint_id=mint_id,
                 db=db)
    release_lease(seat=seat, token=lease["token"], db=db)
    return {"recovered": True, "epoch": epoch, "mint_id": mint_id,
            "minted_repair": minted_repair, "lease_token": lease["token"]}


# ---- (d) AUTHENTICATED BREAK-GLASS (F5 — FREEZE-not-accept) ------------------

def open_break_glass(*, actor_id: str, reason: str, authorized_by: str,
                     db: str = DEFAULT_DB, alert=None) -> dict:
    """Open an authenticated, attributable break-glass event (A2-5) — the
    replacement for the fail-OPEN kill-file at the ENFORCEMENT layer. Requires
    `authorized_by` (an UNAUTHENTICATED open is itself REJECTED — anti-abuse).
    Records a durable cv4_break_glass row and raises the SAME blocked-feedback
    alert as a stuck-in-repair seat (Telegram/approvals) so a break-glass can
    NEVER be a silent local bypass. While open, authority writes FREEZE
    (enforce_or_freeze), they do NOT accept-all. Returns the row."""
    if not (actor_id and str(actor_id).strip()):
        raise WriteFenceRejected("open_break_glass FREEZE: empty actor_id.")
    if not (authorized_by and str(authorized_by).strip()):
        raise WriteFenceRejected(
            "open_break_glass REJECTED: no authorized_by — an unauthenticated "
            "break-glass is refused (anti-abuse; break-glass is attributable).")
    opened_at = _now()
    con = db_connect.connect(db, timeout=5.0)
    try:
        cur = con.execute(
            "INSERT INTO cv4_break_glass (actor_id, reason, authorized_by, "
            "opened_at, closed_at) VALUES (?,?,?,?,NULL)",
            (actor_id, reason, authorized_by, opened_at))
        con.commit()
        bg_id = cur.lastrowid
    finally:
        con.close()
    if alert is not None:
        try:
            alert(kind="cv4_break_glass_opened", actor_id=actor_id,
                  reason=reason, authorized_by=authorized_by, opened_at=opened_at,
                  bg_id=bg_id)
        except Exception:
            pass   # alerting must never break the record itself
    return {"id": bg_id, "actor_id": actor_id, "reason": reason,
            "authorized_by": authorized_by, "opened_at": opened_at}


def close_break_glass(*, bg_id: int, db: str = DEFAULT_DB) -> None:
    """Close a break-glass event (attributable, idempotent — a 2nd close is a
    no-op that preserves the original closed_at)."""
    con = db_connect.connect(db, timeout=5.0)
    try:
        con.execute(
            "UPDATE cv4_break_glass SET closed_at=? WHERE id=? AND closed_at IS NULL",
            (_now(), bg_id))
        con.commit()
    finally:
        con.close()


def is_break_glass_open(db: str = DEFAULT_DB) -> bool:
    con = db_connect.connect(db, timeout=5.0)
    try:
        return con.execute(
            "SELECT 1 FROM cv4_break_glass WHERE closed_at IS NULL LIMIT 1"
        ).fetchone() is not None
    finally:
        con.close()


def enforce_or_freeze(*, seat: str, db: str = DEFAULT_DB) -> None:
    """While break-glass is OPEN, authority writes FREEZE (fail-CLOSED to a safe
    halt) rather than silently accept — the exact defect of the old fail-open
    kill-file (A2-5). Callers of the enforcement path invoke this first; an open
    break-glass halts the fleet's authority mutations, it does not open them."""
    if is_break_glass_open(db=db):
        raise WriteFenceRejected(
            f"enforce_or_freeze FREEZE: break-glass is OPEN — authority writes for "
            f"seat {seat!r} are HALTED (fail-CLOSED). Close the break-glass event "
            f"to resume; disabling enforcement never silently accepts.")


# ---- WIRE authorize_write (F7 stale-obs, F8 operator-asserted, F9 fail-closed) ----

_WEAKER_PROVENANCE = frozenset({"operator-asserted"})


def authorize_write(*, seat: str, lease_token, epoch, authorized_by: str,
                    record=None, store: str = None, key: str = None,
                    sid_source: str = None, observation=None,
                    db: str = DEFAULT_DB) -> dict:
    """The enforcement decision seam (P2/A2-4). A write is authorized ONLY by:
      • a controller-MINTED run (the (seat, epoch) exists in cv4_seat_epochs), AND
      • an ACTIVE seat epoch (>= the current max minted epoch for the seat), AND
      • a VALID lease (lease_held), AND
      • an authorization record (authorized_by present).
    Anything else FREEZES (WriteFenceRejected) — fail-CLOSED, never silent.

    F7 (A2-8): `observation` (session-index last_active, any scanned plane) is
      PROVENANCE ONLY — it can never grant, and never changes an accept/reject.
    F8 (A2-4): `sid_source` is provenance/flag only. operator-asserted is ACCEPTED
      but flagged weaker_provenance (never bricked, never load-bearing); it cannot
      grant on its own.
    F9: an OPEN break-glass FREEZES (enforce_or_freeze); a ledgered seat whose
      write lacks `_fence` FREEZES (reject_if_downgrade). This function RAISES a
      typed WriteFenceRejected (an explicit fail-closed decision) — the SHADOW leg
      (authorize_write_shadow) wraps it so a live writer is never broken (F9).

    Returns {authorized: True, seat, epoch, weaker_provenance} on accept.
    STILL SHADOW: wired + provable, but nothing arms it (separate the operator gate)."""
    # F9: break-glass halts everything first (fail-closed).
    enforce_or_freeze(seat=seat, db=db)

    if not (authorized_by and str(authorized_by).strip()):
        raise WriteFenceRejected(
            f"authorize_write FREEZE: seat {seat!r} — no authorized_by "
            f"(authorship is required; A2-4).")
    if not (lease_token and str(lease_token).strip()) or epoch is None:
        # F7/F8: no lease/epoch => FREEZE regardless of any observation/sid_source.
        raise WriteFenceRejected(
            f"authorize_write FREEZE: seat {seat!r} — authority requires a valid "
            f"lease + active epoch; an observation plane / sid_source can NEVER "
            f"grant (A2-4/A2-8). obs={observation!r} sid_source={sid_source!r}.")
    if not lease_held(seat, lease_token, db=db):
        raise WriteFenceRejected(
            f"authorize_write FREEZE: seat {seat!r} — lease {lease_token!r} is not "
            f"held (expired/released/wrong). Only the lease-holder writes.")

    # controller-minted run + ACTIVE epoch: (seat, epoch) must exist AND be the
    # current max (a stale/older epoch is a superseded run).
    con = db_connect.connect(db, timeout=5.0)
    try:
        row = con.execute(
            "SELECT MAX(epoch) FROM cv4_seat_epochs WHERE seat=?", (seat,)
        ).fetchone()
        current = row[0]
        minted = con.execute(
            "SELECT 1 FROM cv4_seat_epochs WHERE seat=? AND epoch=?",
            (seat, epoch)).fetchone() is not None
    finally:
        con.close()
    if not minted:
        raise WriteFenceRejected(
            f"authorize_write FREEZE: seat {seat!r} epoch {epoch!r} was not minted "
            f"by the controller (no cv4_seat_epochs row) — not an authority run.")
    if current is not None and epoch < current:
        raise WriteFenceRejected(
            f"authorize_write FREEZE: seat {seat!r} epoch {epoch} is STALE "
            f"(current minted epoch is {current}). Superseded run.")

    # F9: anti-downgrade on the actual record (if provided).
    if record is not None and store is not None and key is not None:
        reject_if_downgrade(seat=seat, store=store, key=key, record=record, db=db)

    # F8: sid_source is provenance only — flag operator-asserted, never brick.
    weaker = str(sid_source) in _WEAKER_PROVENANCE if sid_source else False
    return {"authorized": True, "seat": seat, "epoch": epoch,
            "weaker_provenance": weaker}


def authorize_write_shadow(**kwargs) -> dict:
    """SHADOW leg (F9): wrap authorize_write so it ALWAYS returns a decision and
    NEVER raises into a live writer. This is what a real writer would call while
    the fence is in shadow — it observes the would-decision without any power to
    break the write (the whole matrix's swallow-by-return contract). Enforcement
    (letting a False decision actually block) is a separate per-writer the operator gate."""
    try:
        auth = authorize_write(**kwargs)
        return {"authorized": True, "seat": auth["seat"], "epoch": auth["epoch"],
                "weaker_provenance": auth["weaker_provenance"], "reason": "ok"}
    except WriteFenceRejected as e:
        return {"authorized": False, "reason": str(e)}
    except Exception as e:                    # a broken guard must never break the write
        return {"authorized": False, "reason": f"guard_error: {e!r}"}
