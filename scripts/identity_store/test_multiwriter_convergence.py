"""Piece-4 (RED/GREEN) — U17: N concurrent writers converge, zero lost updates.

Proves the factory's ``busy_timeout`` actually prevents lost writes (not just FK
enforcement): under WAL, SQLite still serializes WRITERS, so a second writer
taking ``BEGIN IMMEDIATE`` while another holds the write lock must WAIT
(busy_timeout) rather than fail with "database is locked" and drop its update.

* the real test hammers N threads (each its own factory connection) and asserts
  every update landed and the DB converged;
* the negative control uses raw connections with a ZERO busy_timeout under the
  same contention and shows it DOES hit "database is locked" — proving the
  factory setting is load-bearing;
* a mixed test runs the three writer TYPES (swap txn + sid attribution + status
  shim) concurrently and asserts all converge.
"""
import sqlite3
import threading
import time

import pytest

from scripts.identity_store import orchestra_db, shims


def _init(tmp_path):
    dbp = str(tmp_path / "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    return dbp


def _seed_seat(conn, root, gen=1, sid=None, model="m"):
    conn.execute("INSERT INTO lineages (root, tier, runtime, purpose) "
                 "VALUES (?,?,?,'init')", (root, "T2", "claude"))
    gid = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model) "
        "VALUES (?,?,?,?)", (root, gen, sid, model)).lastrowid
    conn.execute(
        "INSERT INTO canonical (root, generation_id, tmux_session, status) "
        "VALUES (?,?,?,'online')", (root, gid, root))
    return gid


def test_concurrent_writers_no_lost_updates(tmp_path):
    dbp = _init(tmp_path)
    setup = orchestra_db.get_connection(dbp)
    N, K = 8, 40
    for t in range(N):
        _seed_seat(setup, f"seat-{t}")
    setup.close()

    errors = []

    def worker(t):
        c = orchestra_db.get_connection(dbp)
        try:
            for i in range(K):
                shims.registry_update(c, f"seat-{t}", {"purpose": f"{t}-{i}"})
        except Exception as e:                      # noqa: BLE001
            errors.append(repr(e))
        finally:
            c.close()

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(N)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)

    assert errors == [], f"factory busy_timeout must prevent lost writes: {errors[:3]}"
    check = orchestra_db.get_connection(dbp)
    for t in range(N):
        assert check.execute("SELECT purpose FROM lineages WHERE root=?",
                             (f"seat-{t}",)).fetchone()[0] == f"{t}-{K - 1}"
    check.close()


def _seed_x(dbp):
    setup = orchestra_db.get_connection(dbp)
    setup.execute("INSERT INTO lineages (root, tier, runtime, purpose) "
                  "VALUES ('x','T2','claude','init')")
    setup.close()


def test_zero_busy_timeout_raises_when_write_lock_held(tmp_path):
    """Deterministic negative control: while one connection HOLDS the write lock
    (open BEGIN IMMEDIATE), a raw connection with busy_timeout=0 taking
    BEGIN IMMEDIATE fails IMMEDIATELY with 'database is locked' — the exact lost
    write the factory's busy_timeout prevents."""
    dbp = _init(tmp_path)
    _seed_x(dbp)
    holder = orchestra_db.get_connection(dbp)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("UPDATE lineages SET purpose='held' WHERE root='x'")
    try:
        raw = sqlite3.connect(dbp)      # busy_timeout=0, no factory
        raw.isolation_level = None
        with pytest.raises(sqlite3.OperationalError):
            raw.execute("BEGIN IMMEDIATE")
        raw.close()
    finally:
        holder.execute("COMMIT")
        holder.close()


def test_factory_busy_timeout_waits_for_write_lock(tmp_path):
    """The positive half: a FACTORY connection (busy_timeout=10000) taking
    BEGIN IMMEDIATE against a held lock WAITS instead of failing, and succeeds
    once the holder commits — no lost write."""
    dbp = _init(tmp_path)
    _seed_x(dbp)
    holder = orchestra_db.get_connection(dbp)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("UPDATE lineages SET purpose='held' WHERE root='x'")

    result = {}

    def waiter():
        c = orchestra_db.get_connection(dbp)   # busy_timeout=10000
        try:
            c.execute("BEGIN IMMEDIATE")        # blocks until the holder commits
            c.execute("UPDATE lineages SET purpose='waited' WHERE root='x'")
            c.execute("COMMIT")
            result["ok"] = True
        except Exception as e:                  # noqa: BLE001
            result["err"] = repr(e)
        finally:
            c.close()

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.3)          # ensure the waiter is blocked on the write lock
    holder.execute("COMMIT")
    holder.close()
    t.join(timeout=15)

    assert result.get("ok"), f"factory conn must WAIT then succeed, got {result}"
    check = orchestra_db.get_connection(dbp)
    assert check.execute("SELECT purpose FROM lineages WHERE root='x'"
                        ).fetchone()[0] == "waited"
    check.close()


def test_mixed_writer_types_converge(tmp_path):
    dbp = _init(tmp_path)
    setup = orchestra_db.get_connection(dbp)
    a_blue = _seed_seat(setup, "seat-swap", gen=1, sid="swap-sid-1")
    _seed_seat(setup, "seat-attr", gen=1, sid=None)
    _seed_seat(setup, "seat-status", gen=1, sid="status-sid")
    setup.close()

    errors = []
    SW, AT, ST = 20, 20, 20

    def swapper():
        c = orchestra_db.get_connection(dbp)
        try:
            blue = a_blue
            for g in range(2, 2 + SW):
                res = orchestra_db.execute_swap(
                    c, "seat-swap",
                    green={"generation": g, "session_id": f"swap-sid-{g}",
                           "model": "m"},
                    blue_generation_id=blue, now=f"t{g}")
                blue = res["green_generation_id"]
        except Exception as e:                      # noqa: BLE001
            errors.append(("swap", repr(e)))
        finally:
            c.close()

    def attributor():
        c = orchestra_db.get_connection(dbp)
        try:
            for i in range(AT):
                shims.sessions_update(c, "seat-attr", {"session_id": f"attr-sid-{i}"})
        except Exception as e:                      # noqa: BLE001
            errors.append(("attr", repr(e)))
        finally:
            c.close()

    def statuser():
        c = orchestra_db.get_connection(dbp)
        try:
            for i in range(ST):
                shims.registry_update(c, "seat-status", {"purpose": f"p-{i}"})
        except Exception as e:                      # noqa: BLE001
            errors.append(("status", repr(e)))
        finally:
            c.close()

    threads = [threading.Thread(target=fn)
               for fn in (swapper, attributor, statuser)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)

    assert errors == [], f"mixed writer types must converge: {errors}"
    check = orchestra_db.get_connection(dbp)
    # swap advanced canonical to the last generation
    assert check.execute(
        "SELECT g.generation FROM canonical c JOIN generations g "
        "ON g.id=c.generation_id WHERE c.root='seat-swap'").fetchone()[0] == 1 + SW
    # attribution landed the last sid
    assert check.execute(
        "SELECT g.session_id FROM canonical c JOIN generations g "
        "ON g.id=c.generation_id WHERE c.root='seat-attr'").fetchone()[0] == f"attr-sid-{AT - 1}"
    # status landed the last purpose
    assert check.execute("SELECT purpose FROM lineages WHERE root='seat-status'"
                        ).fetchone()[0] == f"p-{ST - 1}"
    check.close()
