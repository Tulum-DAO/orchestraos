"""db_connect — the thin shared tasks.db connection contract (B1-thin, R7a interim).

*the operator-directed 2026-08-25 (all-model-parity GO msg_76adf2ab, gm ruling msg_4ae9eab9).*

HONEST FRAMING — READ THIS: this is INTERIM ROBUSTNESS, **not** a corruption fix and **not**
single-writer discipline. It reduces the lock-contention surface (a heterogeneous set of ~21 bare
writers open tasks.db with SQLite's defaults — a 5s busy timeout and DELETE journal — so concurrent
writers throw `database is locked` and, under the main/WAL-inconsistency class #3c diagnosed in
a9dc7c422, can interleave badly). Applying WAL + a 30s busy_timeout + synchronous=NORMAL makes those
writers wait-and-retry instead of erroring, and lets readers not block writers. It does NOT make
tasks.db single-writer. The DURABLE fix is Lane-A (DEC-1787516372 — a true single write path). This
module is the bridge until Lane-A lands; do not describe it as "the corruption fix."

Contract: `connect(path=TASKS_DB, *, timeout=30.0)` returns an sqlite3.Connection with, applied at
open time:
  - PRAGMA journal_mode=WAL         (readers don't block the writer; one shared WAL)
  - PRAGMA busy_timeout=30000       (30s wait-for-lock instead of the 5s default -> fewer 'locked')
  - PRAGMA synchronous=NORMAL       (WAL-safe durability at lower fsync cost; NORMAL is the WAL norm)

WAL is a DATABASE-LEVEL persistent setting (survives across connections once set), so a bare reader
that never calls this still benefits from WAL mode; busy_timeout + synchronous are PER-CONNECTION and
only apply to callers that route through here — which is the point of rolling the bare writers onto it.
"""
import os
import sqlite3

_ORCH = os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))
TASKS_DB = os.path.join(_ORCH, "state", "tasks.db")

BUSY_TIMEOUT_MS = 30000
SYNCHRONOUS = "NORMAL"


def connect(path=None, *, timeout=30.0):
    """Open tasks.db with the B1-thin robustness pragmas. Drop-in for sqlite3.connect(path).

    `timeout` is the sqlite3 driver-level busy timeout (seconds); we ALSO set PRAGMA busy_timeout
    (ms) so the wait applies uniformly whether a statement blocks in C or via the driver.
    """
    conn = sqlite3.connect(path or TASKS_DB, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute(f"PRAGMA synchronous={SYNCHRONOUS}")
    return conn
