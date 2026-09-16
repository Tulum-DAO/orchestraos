"""RED (BG leg-(ii) P0.2 — swap-CAS: repoint canonical only if it still points to the
expected blue generation).

execute_swap repointed canonical.generation_id -> green UNCONDITIONALLY (INSERT ... ON
CONFLICT(root) DO UPDATE). Under concurrency (two drivers) or a stale/double swap that would
silently clobber write-truth: whoever writes last wins, and a swap could overwrite a canonical
that had already advanced. P0.2: when an expected ``blue_generation_id`` is given, repoint via
a compare-and-swap (UPDATE ... WHERE generation_id == expected_blue); 0 rows updated -> raise
SwapCASError (fail-closed; the BEGIN IMMEDIATE txn rolls back green + all documents).

DELIVERY-CRITICAL. Drives the REAL execute_swap against a REAL orchestra-registry.db and
asserts the on-disk canonical effect (leg-(i) lesson: verify the decisive seam by effect).
"""
import os
import sys

import pytest

sys.path.insert(0, "scripts")
from identity_store.orchestra_db import (  # noqa: E402
    execute_swap, get_connection, init_db)

ROOT = "swap-cas-victim"


def _db(tmp_path):
    db = os.path.join(str(tmp_path), "orchestra-registry.db")
    init_db(db)
    return db


def _seed(conn, canonical_gen_id_of):
    """Insert lineage + blue(gen2) + a THIRD gen(gen9); point canonical at the row id
    chosen by ``canonical_gen_id_of(blue_id, other_id)``. Returns (blue_id, other_id)."""
    conn.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)",
                 (ROOT, "T2", "claude"))
    blue = conn.execute("INSERT INTO generations (root, generation, model) "
                        "VALUES (?, 2, 'claude-opus-4-8[1m]')", (ROOT,)).lastrowid
    other = conn.execute("INSERT INTO generations (root, generation, model) "
                         "VALUES (?, 9, 'claude-opus-4-8[1m]')", (ROOT,)).lastrowid
    canonical_id = canonical_gen_id_of(blue, other)
    conn.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
                 "VALUES (?, ?, ?, 'online')", (ROOT, canonical_id, ROOT))
    return blue, other


def _canonical_gen_id(conn):
    return conn.execute("SELECT generation_id FROM canonical WHERE root=?",
                        (ROOT,)).fetchone()[0]


def _green():
    return {"generation": 3, "model": "claude-opus-4-8[1m]", "session_id": "green-sid-3"}


# ── CAS passes when canonical still points to the expected blue ────────────────

def test_swap_cas_repoints_when_canonical_is_blue(tmp_path):
    conn = get_connection(_db(tmp_path))
    try:
        blue, _ = _seed(conn, lambda b, o: b)          # canonical -> blue
        out = execute_swap(conn, ROOT, _green(), blue_generation_id=blue)
        assert _canonical_gen_id(conn) == out["green_generation_id"], \
            "CAS should repoint canonical to green when it still pointed to blue"
        assert _canonical_gen_id(conn) != blue
    finally:
        conn.close()


# ── CAS RAISES + rolls back when canonical already moved off the expected blue ─

def test_swap_cas_raises_and_rolls_back_when_canonical_moved(tmp_path):
    from identity_store.orchestra_db import SwapCASError
    conn = get_connection(_db(tmp_path))
    try:
        blue, other = _seed(conn, lambda b, o: o)      # canonical -> OTHER (already moved)
        with pytest.raises(SwapCASError):
            execute_swap(conn, ROOT, _green(), blue_generation_id=blue)
        # fail-closed: canonical UNCHANGED (still 'other'), and the green gen3 row was
        # NOT inserted (the BEGIN IMMEDIATE txn rolled back).
        assert _canonical_gen_id(conn) == other, "canonical must be untouched on CAS fail"
        g3 = conn.execute("SELECT COUNT(*) FROM generations WHERE root=? AND generation=3",
                          (ROOT,)).fetchone()[0]
        assert g3 == 0, "the whole swap txn must roll back — no partial green insert"
    finally:
        conn.close()


# ── back-compat: no expected blue => unconditional upsert (fresh promote) ──────

def test_swap_no_blue_id_unconditional_upsert(tmp_path):
    conn = get_connection(_db(tmp_path))
    try:
        conn.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)",
                     (ROOT, "T2", "claude"))
        # no canonical row yet; a None blue_generation_id must still upsert canonical
        out = execute_swap(conn, ROOT, _green(), blue_generation_id=None)
        assert _canonical_gen_id(conn) == out["green_generation_id"]
    finally:
        conn.close()
