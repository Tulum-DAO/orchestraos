"""fence.py — the LOG-ONLY epoch fence for pane writes.

One definition:
    fenced(root, generation_id) := generation_id == canonical.generation_id(root)
read DB-first from state/orchestra-registry.db. This RECORDS, never blocks: a writer calls
check_pane() before it types into a pane; on mismatch one row lands in fencing_violations and a
Violation is returned; the caller's write proceeds unchanged. Nothing here ever raises into a
writer — a fence that can crash a delivery is worse than no fence.

Why a pane -> generation mapping and not the seat name: a `-gN` pane can be canonical, and a
retired `-genN` pane can still be alive and receiving mail. canonical.tmux_session is the only
witness to which pane the lineage currently means. A session the DB does not know is recorded
with writer_generation_id NULL: counted, not invisible (the count of unknown writers is itself a
measurement).
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from dataclasses import dataclass

REGISTRY = os.path.join("state", "orchestra-registry.db")

_DDL = """
CREATE TABLE IF NOT EXISTS fencing_violations (
  id                      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts                      REAL    NOT NULL,
  seam                    TEXT    NOT NULL,
  session                 TEXT,
  root                    TEXT,
  writer_generation_id    INTEGER,
  canonical_generation_id INTEGER,
  detail                  TEXT
)
"""


@dataclass(frozen=True)
class Violation:
    seam: str
    session: str | None
    root: str | None
    writer_generation_id: int | None
    canonical_generation_id: int | None
    detail: str


def _orchestra_dir(orchestra_dir):
    if orchestra_dir:
        return orchestra_dir
    try:
        from orchestra_cli.settings import data_dir
        return str(data_dir())
    except Exception:  # noqa: BLE001 — no settings importable: the env or nothing
        return os.environ.get("ORCHESTRA_DIR", "")


def _resolve_pane(con, session):
    """(root, generation_id_of_this_pane, canonical_generation_id) for a tmux session name.

    Exact match on canonical.tmux_session first (the pane the lineage currently means). Else
    the session is a gen-suffixed sibling '<root>-g<N>' / '<root>-gen<N>' of a known root: its
    generation row is looked up by number; the canonical pointer is the root's. Else unknown."""
    row = con.execute("SELECT root, generation_id FROM canonical WHERE tmux_session=?",
                      (session,)).fetchone()
    if row:
        return row[0], row[1], row[1]
    m = re.match(r"^(.*)-g(?:en)?(\d+)$", session)
    if m:
        root, gen = m.group(1), int(m.group(2))
        can = con.execute("SELECT generation_id FROM canonical WHERE root=?", (root,)).fetchone()
        if can:
            g = con.execute("SELECT id FROM generations WHERE root=? AND generation=?",
                            (root, gen)).fetchone()
            return root, (g[0] if g else None), can[0]
    can = con.execute("SELECT generation_id FROM canonical WHERE root=?", (session,)).fetchone()
    if can:   # the bare root name, but canonical lives under another pane: this is NOT that pane
        return session, None, can[0]
    return None, None, None


def _refuse_live_write_under_test(orchestra_dir) -> bool:
    """Under pytest, never WRITE a violation into a real data dir (the dirs the repo-root conftest
    records as protected, orchestra_cli.settings.protected_data_dirs). A sandboxed dir records
    normally (the fence's own tests depend on it), and production is never affected because
    PYTEST_CURRENT_TEST is unset there."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    try:
        from orchestra_cli.settings import protected_data_dirs
        target = os.path.realpath(orchestra_dir)
        for root in protected_data_dirs():
            if target == root or target.startswith(root.rstrip(os.sep) + os.sep):
                print(f"[fence] refusing to write a violation to a real data dir from a test "
                      f"(ORCHESTRA_DIR={orchestra_dir}); sandbox ORCHESTRA_DIR in the test",
                      flush=True)
                return True
    except Exception:  # noqa: BLE001 — cannot tell: refuse, a test must never write a real store
        return True
    return False


def check_pane(session, *, seam, orchestra_dir=None, now=None,
               allow_provisional=False) -> Violation | None:
    """Log-only fence for a pane write. Returns a Violation (and records it) when `session` is
    not the canonical pane of its lineage, None when it is. NEVER raises; an absent or unreadable
    registry returns None (cannot measure is not measured clean, but a fence must not break a
    delivery). The registry is opened read-write WITHOUT create, so a missing DB stays missing.

    allow_provisional=True is for a not-yet-promoted successor ONLY: by design a live,
    non-canonical generation, so writing to it is expected. A pane whose generation is RETIRED
    (retired_at set) or unknown is still a violation in that mode."""
    _dir = _orchestra_dir(orchestra_dir)
    if not _dir or _refuse_live_write_under_test(_dir):
        # Returning None also refuses to CLAIM a violation: a caller must never act on a
        # measurement that was not recorded (one signal, one meaning).
        return None
    path = os.path.join(_dir, REGISTRY)
    try:
        con = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5)
        try:
            root, writer_gid, canon_gid = _resolve_pane(con, session)
            if root is not None and writer_gid is not None and writer_gid == canon_gid:
                return None
            if allow_provisional and root is not None and writer_gid is not None:
                ret = con.execute("SELECT retired_at FROM generations WHERE id=?",
                                  (writer_gid,)).fetchone()
                if ret is not None and ret[0] is None:
                    return None          # a live provisional successor, not a leak
            if root is None:
                detail = f"session {session!r} is not a canonical pane nor a -gN pane of any known root"
            elif writer_gid is None:
                detail = (f"session {session!r} names root {root!r} but is not its canonical pane "
                          f"(canonical generation_id={canon_gid})")
            else:
                detail = (f"session {session!r} is generation_id={writer_gid} of {root!r}; canonical "
                          f"is generation_id={canon_gid} (a non-canonical pane is being written to)")
            v = Violation(seam=seam, session=session, root=root, writer_generation_id=writer_gid,
                          canonical_generation_id=canon_gid, detail=detail)
            con.execute(_DDL)
            con.execute("INSERT INTO fencing_violations (ts, seam, session, root, writer_generation_id, "
                        "canonical_generation_id, detail) VALUES (?,?,?,?,?,?,?)",
                        (time.time() if now is None else now, seam, session, root, writer_gid,
                         canon_gid, detail))
            con.commit()
            return v
        finally:
            con.close()
    except Exception:  # noqa: BLE001 — a fence must never break the write it observes
        return None
