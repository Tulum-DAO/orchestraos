#!/usr/bin/env python3
"""Continuity v4 — soak EPOCH registry (additive; Amendment A correction 2).

The drift soak appends dated JSONL to state/cv4-soak/<date>.jsonl but had no
formal epoch concept, so the contaminated observation window could not be closed
and a clean fenced window could not be certified against the 7-day gate.

This module adds ONE additive table `cv4_soak_epochs` (in tasks.db, same pattern
as the other cv4_* tables). Nothing arms. Closing an epoch is a BOUNDARY (status
flip + closed_at timestamp), NEVER a delete — the contaminated observations and
their epoch row are preserved as evidence. Exactly one epoch may be `open`.

`soak.py` calls `current_open_epoch_safe()` read-only every run to tag its record
with the active `soak_epoch_id`; only records tagged with the open epoch count
toward the 7-day gate.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_scripts_dbc = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _scripts_dbc not in sys.path:
    sys.path.insert(0, _scripts_dbc)
import db_connect  # B1-thin: shared tasks.db connect (WAL + 30s busy_timeout)

ORCHESTRA_DIR = Path(os.environ.get(
    "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
DEFAULT_DB = str(ORCHESTRA_DIR / "state" / "tasks.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cv4_soak_epochs (
    epoch_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    closed_at     TEXT,
    status        TEXT NOT NULL CHECK (status IN ('open','closed')),
    fencing_state TEXT,
    source_hashes TEXT,
    note          TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    con = db_connect.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def ensure_epoch_schema(db_path: str = DEFAULT_DB) -> None:
    con = _connect(db_path)
    try:
        con.executescript(_SCHEMA)   # additive; IF NOT EXISTS -> idempotent
        con.commit()
    finally:
        con.close()


def _row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("fencing_state", "source_hashes"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except (ValueError, TypeError):
                pass
    return d


def open_epoch(db_path: str, *, fencing_state: dict | None,
               source_hashes: dict | None, note: str | None = None) -> int:
    """Open a new epoch. Refuses if an epoch is already open (exactly one open)."""
    con = _connect(db_path)
    try:
        with con:
            existing = con.execute(
                "SELECT epoch_id FROM cv4_soak_epochs WHERE status='open'"
            ).fetchone()
            if existing:
                raise RuntimeError(
                    f"an epoch is already open (epoch_id={existing['epoch_id']}); "
                    "close it before opening a new one")
            cur = con.execute(
                "INSERT INTO cv4_soak_epochs "
                "(started_at, status, fencing_state, source_hashes, note) "
                "VALUES (?, 'open', ?, ?, ?)",
                (_now(), json.dumps(fencing_state or {}),
                 json.dumps(source_hashes or {}), note))
            return int(cur.lastrowid)
    finally:
        con.close()


def close_epoch(db_path: str, epoch_id: int, *, note: str | None = None) -> None:
    """Close an epoch: a BOUNDARY (status->closed + closed_at), never a delete."""
    con = _connect(db_path)
    try:
        with con:
            row = con.execute(
                "SELECT status, note FROM cv4_soak_epochs WHERE epoch_id=?",
                (epoch_id,)).fetchone()
            if row is None:
                raise KeyError(f"no such epoch_id={epoch_id}")
            if row["status"] == "closed":
                raise RuntimeError(f"epoch_id={epoch_id} already closed")
            merged = row["note"]
            if note:
                merged = f"{merged} | closed: {note}" if merged else f"closed: {note}"
            con.execute(
                "UPDATE cv4_soak_epochs SET status='closed', closed_at=?, note=? "
                "WHERE epoch_id=?", (_now(), merged, epoch_id))
    finally:
        con.close()


def current_open_epoch(db_path: str = DEFAULT_DB) -> dict | None:
    con = _connect(db_path)
    try:
        r = con.execute(
            "SELECT * FROM cv4_soak_epochs WHERE status='open' "
            "ORDER BY epoch_id DESC LIMIT 1").fetchone()
        return _row_to_dict(r) if r else None
    finally:
        con.close()


def current_open_epoch_safe(db_path: str = DEFAULT_DB) -> dict | None:
    """Read-only lookup for soak.py: returns None (never raises) if the table or
    db does not exist yet, so a soak run before the first epoch cannot crash."""
    try:
        return current_open_epoch(db_path)
    except sqlite3.OperationalError:
        return None
    except sqlite3.Error:
        return None


def list_epochs(db_path: str = DEFAULT_DB) -> list[dict]:
    con = _connect(db_path)
    try:
        return [_row_to_dict(r) for r in con.execute(
            "SELECT * FROM cv4_soak_epochs ORDER BY epoch_id").fetchall()]
    finally:
        con.close()


def rotate_epoch(db_path: str, *, fencing_state: dict | None,
                 source_hashes: dict | None, note: str | None = None,
                 contaminated_note: str | None = None) -> int:
    """Atomically close the current window and open a fresh one.

    If an epoch is already open, close it. If NONE exists yet (first rotation),
    record the pre-fence CONTAMINATED observation window as a closed epoch (so it
    is on the record, its JSONL preserved) before opening the clean epoch.
    Returns the new open epoch_id.
    """
    con = _connect(db_path)
    try:
        with con:
            openrow = con.execute(
                "SELECT epoch_id FROM cv4_soak_epochs WHERE status='open'"
            ).fetchone()
            now = _now()
            if openrow:
                con.execute(
                    "UPDATE cv4_soak_epochs SET status='closed', closed_at=?, "
                    "note=COALESCE(note,'') || ? WHERE epoch_id=?",
                    (now, f" | rotated: {note or ''}", openrow["epoch_id"]))
            else:
                con.execute(
                    "INSERT INTO cv4_soak_epochs "
                    "(started_at, closed_at, status, fencing_state, "
                    " source_hashes, note) VALUES (?, ?, 'closed', ?, ?, ?)",
                    (now, now, json.dumps({"mac_fenced": False}),
                     json.dumps({}),
                     contaminated_note or
                     "contaminated pre-fence observations (preserved in "
                     "state/cv4-soak/*.jsonl; not counted toward the 7-day gate)"))
            cur = con.execute(
                "INSERT INTO cv4_soak_epochs "
                "(started_at, status, fencing_state, source_hashes, note) "
                "VALUES (?, 'open', ?, ?, ?)",
                (now, json.dumps(fencing_state or {}),
                 json.dumps(source_hashes or {}), note))
            return int(cur.lastrowid)
    finally:
        con.close()


# --------------------------------------------------------------------- CLI
def _source_hashes() -> dict:
    import hashlib
    planes = [ORCHESTRA_DIR / "registry.json",
              ORCHESTRA_DIR / "state" / "agent-sessions.json"]
    adir = ORCHESTRA_DIR / "state" / "agents"
    if adir.is_dir():
        planes += sorted(adir.glob("*.json"))
    out = {}
    for p in planes:
        if p.exists():
            out[str(p.relative_to(ORCHESTRA_DIR))] = \
                hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cv4 soak epoch registry (additive)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("current")
    sub.add_parser("list")
    po = sub.add_parser("open")
    po.add_argument("--note")
    pc = sub.add_parser("close")
    pc.add_argument("epoch_id", type=int)
    pc.add_argument("--note")
    pr = sub.add_parser("rotate")
    pr.add_argument("--note", required=True)
    pr.add_argument("--contaminated-note")
    args = ap.parse_args(argv)

    ensure_epoch_schema()
    if args.cmd == "current":
        print(json.dumps(current_open_epoch(), indent=2, default=str))
    elif args.cmd == "list":
        print(json.dumps(list_epochs(), indent=2, default=str))
    elif args.cmd == "open":
        eid = open_epoch(DEFAULT_DB, fencing_state={"mac_fenced": True},
                         source_hashes=_source_hashes(), note=args.note)
        print(f"opened epoch_id={eid}")
    elif args.cmd == "close":
        close_epoch(DEFAULT_DB, args.epoch_id, note=args.note)
        print(f"closed epoch_id={args.epoch_id}")
    elif args.cmd == "rotate":
        eid = rotate_epoch(DEFAULT_DB, fencing_state={"mac_fenced": True},
                           source_hashes=_source_hashes(), note=args.note,
                           contaminated_note=args.contaminated_note)
        print(f"rotated -> new open epoch_id={eid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
