"""CV4 durable process-instance placement (rung-4, codex §2).

Append-only durable home for the boot_id-inclusive process_fence identity, in the
SAME authority DB as leases/epochs (tasks.db). Three additive tables:

  * cv4_process_instances     — one immutable row per InstanceRecord, PK = the
    fence-derived instance_id. Re-persisting the same identity is idempotent; a
    DIFFERENT identity tuple under the same id is REFUSED (identity is immutable —
    an observation may add facts, never overwrite the tuple).
  * cv4_instance_observations — append-only liveness/observation ledger (many rows
    per instance_id).
  * cv4_rotation              — the rotation join: predecessor_instance_id +
    successor_instance_id + lease_token + epoch. instance_id is the primary
    join/fence key; both legs must reference a persisted instance.

Additive + idempotent (CREATE TABLE IF NOT EXISTS); off the critical path.
"""
from __future__ import annotations

import json
from typing import Optional

import db_connect

from continuity import process_fence as _pf


class InstanceIdentityConflict(RuntimeError):
    """A different identity tuple was written under an existing instance_id."""


class UnknownInstance(RuntimeError):
    """A rotation referenced an instance_id that is not persisted."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cv4_process_instances (
    instance_id  TEXT PRIMARY KEY,
    pid          INTEGER NOT NULL,
    starttime    INTEGER NOT NULL,
    boot_id      TEXT NOT NULL,
    provider     TEXT NOT NULL,
    provider_sid TEXT,
    tmux_session TEXT NOT NULL,
    tmux_pane_id TEXT NOT NULL,
    host_id      TEXT NOT NULL,
    observed_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cv4_instance_observations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT NOT NULL,
    is_alive    INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    facts       TEXT
);

CREATE TABLE IF NOT EXISTS cv4_rotation (
    rotation_id             TEXT PRIMARY KEY,
    predecessor_instance_id TEXT NOT NULL,
    successor_instance_id   TEXT NOT NULL,
    lease_token             TEXT NOT NULL,
    epoch                   INTEGER NOT NULL,
    recorded_at             TEXT
);
"""

_IDENTITY_COLS = ("pid", "starttime", "boot_id", "provider", "provider_sid",
                  "tmux_session", "tmux_pane_id", "host_id", "observed_at")


def ensure_process_instance_schema(db: str) -> None:
    """Create the 3 process-instance tables (additive + idempotent)."""
    con = db_connect.connect(db)
    try:
        con.executescript(_SCHEMA)
        con.commit()
    finally:
        con.close()


def persist_instance(db: str, record: "_pf.InstanceRecord", *,
                     instance_id: Optional[str] = None) -> str:
    """Persist an immutable InstanceRecord row keyed by its fence-derived
    instance_id. Idempotent for an identical identity; raises
    InstanceIdentityConflict if a DIFFERENT tuple already exists under that id.

    `instance_id` override exists ONLY to let a caller (or test) attempt a
    colliding write under a chosen id — the conflict check still fires."""
    iid = instance_id if instance_id is not None else record.instance_id
    row = record.to_dict()
    con = db_connect.connect(db)
    try:
        existing = con.execute(
            "SELECT " + ", ".join(_IDENTITY_COLS) +
            " FROM cv4_process_instances WHERE instance_id=?", (iid,)).fetchone()
        if existing is not None:
            if tuple(existing) != tuple(row[c] for c in _IDENTITY_COLS):
                raise InstanceIdentityConflict(
                    f"instance_id {iid} already persisted with a DIFFERENT "
                    f"identity tuple — identity is immutable")
            return iid  # idempotent: identical identity already present
        con.execute(
            "INSERT INTO cv4_process_instances (instance_id, " +
            ", ".join(_IDENTITY_COLS) + ") VALUES (?" + ", ?" * len(_IDENTITY_COLS)
            + ")", (iid, *[row[c] for c in _IDENTITY_COLS]))
        con.commit()
        return iid
    finally:
        con.close()


def load_instance(db: str, instance_id: str) -> Optional["_pf.InstanceRecord"]:
    con = db_connect.connect(db)
    try:
        r = con.execute(
            "SELECT " + ", ".join(_IDENTITY_COLS) +
            " FROM cv4_process_instances WHERE instance_id=?",
            (instance_id,)).fetchone()
    finally:
        con.close()
    if r is None:
        return None
    d = dict(zip(_IDENTITY_COLS, r))
    return _pf.InstanceRecord.from_dict(d)


def append_observation(db: str, instance_id: str, *, is_alive: bool,
                       observed_at: str, facts: Optional[dict] = None) -> None:
    """Append an observation for a persisted instance (append-only ledger)."""
    con = db_connect.connect(db)
    try:
        con.execute(
            "INSERT INTO cv4_instance_observations "
            "(instance_id, is_alive, observed_at, facts) VALUES (?, ?, ?, ?)",
            (instance_id, 1 if is_alive else 0, observed_at,
             json.dumps(facts) if facts is not None else None))
        con.commit()
    finally:
        con.close()


def load_observations(db: str, instance_id: str) -> list:
    con = db_connect.connect(db)
    try:
        rows = con.execute(
            "SELECT is_alive, observed_at, facts FROM cv4_instance_observations "
            "WHERE instance_id=? ORDER BY id", (instance_id,)).fetchall()
    finally:
        con.close()
    return [{"is_alive": bool(a), "observed_at": t,
             "facts": json.loads(f) if f else None} for a, t, f in rows]


def record_rotation(db: str, *, rotation_id: str, predecessor_instance_id: str,
                    successor_instance_id: str, lease_token: str, epoch: int,
                    recorded_at: Optional[str] = None) -> None:
    """Record the rotation join. Both instance ids must reference a persisted
    instance (fence integrity) — else UnknownInstance."""
    con = db_connect.connect(db)
    try:
        for iid in (predecessor_instance_id, successor_instance_id):
            hit = con.execute(
                "SELECT 1 FROM cv4_process_instances WHERE instance_id=?",
                (iid,)).fetchone()
            if hit is None:
                raise UnknownInstance(
                    f"rotation references unpersisted instance_id {iid}")
        con.execute(
            "INSERT OR REPLACE INTO cv4_rotation (rotation_id, "
            "predecessor_instance_id, successor_instance_id, lease_token, "
            "epoch, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
            (rotation_id, predecessor_instance_id, successor_instance_id,
             lease_token, int(epoch), recorded_at))
        con.commit()
    finally:
        con.close()


def load_rotation(db: str, rotation_id: str) -> Optional[dict]:
    con = db_connect.connect(db)
    try:
        r = con.execute(
            "SELECT predecessor_instance_id, successor_instance_id, "
            "lease_token, epoch FROM cv4_rotation WHERE rotation_id=?",
            (rotation_id,)).fetchone()
    finally:
        con.close()
    if r is None:
        return None
    return {"predecessor_instance_id": r[0], "successor_instance_id": r[1],
            "lease_token": r[2], "epoch": r[3]}
