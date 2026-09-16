"""Piece-2 (RED) — the §2 swap transaction + U14 + U8.

The swap is ONE ``BEGIN IMMEDIATE`` transaction: identity is fully Blue before
COMMIT and fully Green after, with no torn state a reader could observe. This
suite proves:

* correctness — canonical repoints to Green, Blue is retired;
* U14 — Green's ``runtime_state`` row is bootstrapped INSIDE the txn (else a
  ``canonical JOIN runtime_state`` projection drops Green at COMMIT = dark chip);
* atomicity — a crash at ANY statement boundary rolls back to fully-Blue, zero
  repair needed;
* U8 — the same-transaction stale-sid-holder clear (SQLite enforces
  ``UNIQUE(session_id)`` immediately, so the stale holder must be NULLed BEFORE
  the sid is assigned — a naive single UPDATE hits the trap).

RED until ``execute_swap`` / ``attribute_session_id`` exist.
"""
import sqlite3

import pytest

from scripts.identity_store import orchestra_db

ROOT = "orchestra-builder"


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "orchestra-registry.db"
    orchestra_db.init_db(str(p))
    c = orchestra_db.get_connection(str(p))
    yield c
    c.close()


def _seed_blue(conn, root=ROOT, blue_sid="blue-sid"):
    conn.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)",
                 (root, "T2", "claude"))
    blue_id = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model) "
        "VALUES (?,?,?,?)", (root, 1, blue_sid, "model-blue")).lastrowid
    conn.execute(
        "INSERT INTO canonical (root, generation_id, tmux_session, status) "
        "VALUES (?,?,?,?)", (root, blue_id, root, "online"))
    conn.execute(
        "INSERT INTO runtime_state (generation_id, status, last_updated) "
        "VALUES (?,?,?)", (blue_id, "online", "t0"))
    conn.commit()
    return blue_id


def _canonical(conn, root=ROOT):
    return conn.execute("SELECT * FROM canonical WHERE root=?", (root,)).fetchone()


# --- §2 swap correctness ---------------------------------------------------

def test_swap_repoints_canonical_and_retires_blue(conn):
    blue_id = _seed_blue(conn)
    res = orchestra_db.execute_swap(
        conn, ROOT,
        green={"generation": 2, "session_id": "green-sid", "model": "model-green"},
        blue_generation_id=blue_id, now="t1")
    green_id = res["green_generation_id"]
    can = _canonical(conn)
    assert can["generation_id"] == green_id
    assert can["tmux_session"] == ROOT
    blue = conn.execute("SELECT retired_at FROM generations WHERE id=?",
                        (blue_id,)).fetchone()
    assert blue["retired_at"] == "t1"


# --- U14: Green runtime_state bootstrapped INSIDE the swap txn --------------

def test_swap_bootstraps_green_runtime_state_in_txn(conn):
    blue_id = _seed_blue(conn)
    res = orchestra_db.execute_swap(
        conn, ROOT,
        green={"generation": 2, "session_id": "green-sid", "model": "model-green"},
        blue_generation_id=blue_id, now="t1")
    green_id = res["green_generation_id"]
    row = conn.execute(
        "SELECT rs.status FROM canonical c "
        "JOIN runtime_state rs ON rs.generation_id = c.generation_id "
        "WHERE c.root=?", (ROOT,)).fetchone()
    assert row is not None, \
        "Green dropped from canonical JOIN runtime_state = instant dark chip"
    assert row["status"] == "online"


# --- §2 / U2: crash at each statement boundary rolls back to fully-Blue -----

@pytest.mark.parametrize("fail_after", [1, 2, 3, 4, 5])
def test_swap_rollback_on_crash_leaves_blue_canonical(conn, fail_after):
    blue_id = _seed_blue(conn)
    with pytest.raises(RuntimeError):
        orchestra_db.execute_swap(
            conn, ROOT,
            green={"generation": 2, "session_id": "green-sid", "model": "model-green"},
            blue_generation_id=blue_id, now="t1", _fail_after=fail_after)
    can = _canonical(conn)
    assert can["generation_id"] == blue_id, "Blue must remain canonical"
    blue = conn.execute("SELECT retired_at FROM generations WHERE id=?",
                        (blue_id,)).fetchone()
    assert blue["retired_at"] is None, "Blue must not be retired on rollback"
    assert conn.execute(
        "SELECT COUNT(*) FROM generations WHERE generation=2").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM swaps").fetchone()[0] == 0


# --- U8: same-txn stale-sid-holder clear -----------------------------------

def _seed_stale_and_target(conn, root=ROOT, sid="sid-x"):
    conn.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)",
                 (root, "T2", "claude"))
    stale = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model) "
        "VALUES (?,?,?,?)", (root, 1, sid, "m")).lastrowid
    target = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model) "
        "VALUES (?,?,?,?)", (root, 2, None, "m")).lastrowid
    conn.commit()
    return stale, target


def test_attribute_session_id_takes_over_stale_holder(conn):
    stale, target = _seed_stale_and_target(conn)
    orchestra_db.attribute_session_id(conn, target, "sid-x")
    assert conn.execute("SELECT session_id FROM generations WHERE id=?",
                        (target,)).fetchone()[0] == "sid-x"
    assert conn.execute("SELECT session_id FROM generations WHERE id=?",
                        (stale,)).fetchone()[0] is None


def test_naive_single_update_attribution_hits_the_unique_trap(conn):
    """The trap is REAL: assigning the sid without first clearing the stale
    holder violates UNIQUE(session_id). This is exactly what
    attribute_session_id's same-txn NULL-then-assign avoids."""
    _stale, target = _seed_stale_and_target(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE generations SET session_id=? WHERE id=?",
                     ("sid-x", target))
        conn.commit()


def test_swap_clears_stale_holder_of_green_sid(conn):
    """If a crashed predecessor row still holds the sid Green will use, the swap
    txn must clear it in the SAME transaction (the U8 trap arises during a swap
    too, not only reconciler attribution)."""
    blue_id = _seed_blue(conn)
    stale = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model) "
        "VALUES (?,?,?,?)", (ROOT, 99, "green-sid", "m")).lastrowid
    conn.commit()
    res = orchestra_db.execute_swap(
        conn, ROOT,
        green={"generation": 2, "session_id": "green-sid", "model": "model-green"},
        blue_generation_id=blue_id, now="t1")
    green_id = res["green_generation_id"]
    assert conn.execute("SELECT session_id FROM generations WHERE id=?",
                        (green_id,)).fetchone()[0] == "green-sid"
    assert conn.execute("SELECT session_id FROM generations WHERE id=?",
                        (stale,)).fetchone()[0] is None
