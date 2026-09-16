"""U7 (RED) — same-id-corpse and minimal-row states are UNREPRESENTABLE.

The identity substrate's disease is multi-writer, multi-file partial state. Two
incident classes from a single night motivate this:

* minimal-row (dark chip #1): a partial write left a canonical/generation row
  missing its identity fields.
* same-id-corpse (dark chip #2): a dead archive copy kept the canonical id/sid,
  so two rows both claimed the same identity.

These tests assert the schema makes BOTH classes impossible to express — not by
convention (a writer remembering to fill every field) but by NOT NULL / UNIQUE /
PRIMARY KEY / FOREIGN KEY constraints. They exercise piece-1: the schema DDL and
the connection factory (FK enforcement is a per-connection pragma the factory
owns — see the U13 suite).

RED until ``scripts/identity_store/orchestra_db`` exists.
"""
import sqlite3

import pytest

from scripts.identity_store import orchestra_db


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "orchestra-registry.db"
    orchestra_db.init_db(str(p))
    c = orchestra_db.get_connection(str(p))
    yield c
    c.close()


def _make_lineage(conn, root="orchestra-builder"):
    conn.execute(
        "INSERT INTO lineages (root, tier, runtime) VALUES (?, ?, ?)",
        (root, "T2", "claude"),
    )
    conn.commit()


def _make_generation(conn, root="orchestra-builder", generation=1,
                     session_id="sid-1", model="claude-opus-4-8[1m]"):
    cur = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model) "
        "VALUES (?, ?, ?, ?)",
        (root, generation, session_id, model),
    )
    conn.commit()
    return cur.lastrowid


# --- minimal-row (partial write) is UNREPRESENTABLE ------------------------

def test_generation_without_model_is_rejected(conn):
    """A generation row lacking its required identity field cannot exist."""
    _make_lineage(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO generations (root, generation) VALUES (?, ?)",
            ("orchestra-builder", 1),
        )
        conn.commit()


def test_lineage_without_tier_or_runtime_is_rejected(conn):
    """A lineage row lacking tier/runtime (the minimal-row disease at the
    lineage level) is rejected by NOT NULL."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO lineages (root) VALUES (?)", ("bare-root",))
        conn.commit()


def test_canonical_without_tmux_session_is_rejected(conn):
    """The canonical pointer's external-effect field is mandatory — a minimal
    canonical row (the manual-cutover dark-chip class) cannot be written."""
    _make_lineage(conn)
    gid = _make_generation(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO canonical (root, generation_id) VALUES (?, ?)",
            ("orchestra-builder", gid),
        )
        conn.commit()


# --- same-id-corpse (dead archive keeping the canonical id/sid) is
# --- UNREPRESENTABLE -------------------------------------------------------

def test_two_generations_cannot_share_a_session_id(conn):
    """Two rows claiming the same runtime sid (a live row + its dead archive
    corpse) is impossible: session_id is UNIQUE."""
    _make_lineage(conn)
    _make_generation(conn, generation=1, session_id="shared-sid")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO generations (root, generation, session_id, model) "
            "VALUES (?, ?, ?, ?)",
            ("orchestra-builder", 2, "shared-sid", "claude-opus-4-8[1m]"),
        )
        conn.commit()


def test_lineage_cannot_have_two_canonical_pointers(conn):
    """Exactly one canonical pointer per lineage. The classic dead-archive
    corpse that still claims to be canonical cannot coexist with the live one:
    canonical.root is PRIMARY KEY."""
    _make_lineage(conn)
    g1 = _make_generation(conn, generation=1, session_id="sid-1")
    g2 = _make_generation(conn, generation=2, session_id="sid-2")
    conn.execute(
        "INSERT INTO canonical (root, generation_id, tmux_session) "
        "VALUES (?, ?, ?)",
        ("orchestra-builder", g1, "orchestra-builder"),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO canonical (root, generation_id, tmux_session) "
            "VALUES (?, ?, ?)",
            ("orchestra-builder", g2, "orchestra-builder"),
        )
        conn.commit()


def test_duplicate_root_generation_is_rejected(conn):
    """No two generation rows for the same (root, generation) — the archival
    key convention (<root>-genN) becomes a schema invariant."""
    _make_lineage(conn)
    _make_generation(conn, generation=5, session_id="sid-a")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO generations (root, generation, session_id, model) "
            "VALUES (?, ?, ?, ?)",
            ("orchestra-builder", 5, "sid-b", "claude-opus-4-8[1m]"),
        )
        conn.commit()


# --- canonical pointer must reference a real generation (FK; factory-owned) --

def test_canonical_pointer_must_reference_existing_generation(conn):
    """A canonical pointer to a non-existent generation is an FK violation —
    only enforced because the factory turns foreign_keys ON (see U13)."""
    _make_lineage(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO canonical (root, generation_id, tmux_session) "
            "VALUES (?, ?, ?)",
            ("orchestra-builder", 999999, "orchestra-builder"),
        )
        conn.commit()


def test_generation_root_must_reference_existing_lineage(conn):
    """A generation whose root has no lineage row is an FK violation — the
    orphaned-identity class the factory's foreign_keys=ON forbids."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO generations (root, generation, model) "
            "VALUES (?, ?, ?)",
            ("no-such-lineage", 1, "claude-opus-4-8[1m]"),
        )
        conn.commit()
