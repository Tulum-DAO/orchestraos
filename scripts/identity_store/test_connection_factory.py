"""U13 (RED) — connection-factory pragma discipline is LOAD-BEARING.

``PRAGMA foreign_keys`` and ``PRAGMA busy_timeout`` are PER-CONNECTION settings,
not database properties: a raw ``sqlite3.connect`` opens with foreign_keys OFF
and a zero busy_timeout every time, regardless of how the DB was created. If any
shim/daemon/CLI connects directly, FK-orphaned identity rows slip through and
concurrent writers hit ``database is locked`` instead of waiting.

The discipline: every connection goes through one mandatory factory,
``orchestra_db.get_connection()``, which sets WAL + foreign_keys=ON +
busy_timeout=10000 + synchronous=NORMAL. These tests prove the factory is
load-bearing by showing the IDENTICAL FK-violating write is ACCEPTED on a raw
connection (the hazard) and REJECTED through the factory (the guard).

RED until ``scripts/identity_store/orchestra_db`` exists.
"""
import sqlite3

import pytest

from scripts.identity_store import orchestra_db


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "orchestra-registry.db"
    orchestra_db.init_db(str(p))
    return str(p)


def test_factory_sets_all_four_pragmas(db_path):
    conn = orchestra_db.get_connection(db_path)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1, \
            "foreign_keys must be ON"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000, \
            "busy_timeout must be 10000ms"
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal", \
            "journal_mode must be WAL"
        # PRAGMA synchronous: 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1, \
            "synchronous must be NORMAL"
    finally:
        conn.close()


def test_raw_connect_lets_fk_violation_slip_through(db_path):
    """Negative control / hazard demonstration: a raw sqlite3.connect does NOT
    enforce foreign_keys, so an orphaned generation (root with no lineage row)
    is silently accepted. This is exactly the identity-splinter the factory
    exists to prevent."""
    raw = sqlite3.connect(db_path)
    try:
        raw.execute(
            "INSERT INTO generations (root, generation, model) VALUES (?, ?, ?)",
            ("ghost-root", 1, "claude-opus-4-8[1m]"),
        )
        raw.commit()
        n = raw.execute(
            "SELECT COUNT(*) FROM generations WHERE root = 'ghost-root'"
        ).fetchone()[0]
        assert n == 1, "raw connection should let the FK violation through"
    finally:
        raw.close()


def test_factory_rejects_the_same_fk_violation(db_path):
    """The factory path MUST reject the identical write the raw path accepted —
    proving the factory (foreign_keys=ON) is load-bearing, not decorative."""
    conn = orchestra_db.get_connection(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO generations (root, generation, model) "
                "VALUES (?, ?, ?)",
                ("ghost-root", 1, "claude-opus-4-8[1m]"),
            )
            conn.commit()
    finally:
        conn.close()
