"""Piece-4 (RED) — U5: legacy direct-JSON writes are REFUSED + logged; DB shims.

Post-migration the three identity files are projector-owned READ-ONLY artifacts.
A legacy writer must not write them directly — it goes through a DB shim
(registry_update / sessions_update) that writes the store. U5:

* ``refuse_direct_json(path)`` REFUSES (raises) + LOGS any write aimed at a
  managed projection path — guarding by PATH so it also covers the embedded-
  python shell writers' targets (ob rider 2), not just an importable CLI. It does
  NOT over-refuse adjacent files (live-roster.json, agent-state/, handoffs).
* the DB shims write the STORE, never JSON.

RED until ``scripts/identity_store/shims`` exists.
"""
import os

import pytest

from scripts.identity_store import orchestra_db, shims

ROOT = "orchestra-builder"


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "orchestra-registry.db"
    orchestra_db.init_db(str(p))
    c = orchestra_db.get_connection(str(p))
    yield c
    c.close()


def _seed(conn, root=ROOT):
    conn.execute("INSERT INTO lineages (root, tier, runtime, purpose) "
                 "VALUES (?,?,?,?)", (root, "T2", "claude", "old"))
    gid = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model) "
        "VALUES (?,?,?,?)", (root, 1, "sid-1", "model-x")).lastrowid
    conn.execute(
        "INSERT INTO canonical (root, generation_id, tmux_session, status) "
        "VALUES (?,?,?,'online')", (root, gid, root))
    conn.execute(
        "INSERT INTO runtime_state (generation_id, status, last_updated) "
        "VALUES (?,'online','t0')", (gid,))
    conn.commit()
    return gid


# --- U5: refuse-direct-JSON guard ------------------------------------------

@pytest.mark.parametrize("rel", [
    "registry.json",
    "state/agent-sessions.json",
    "state/agents/orchestra-builder.json",
    "/home/testuser/agent-orchestra/registry.json",
])
def test_refuse_direct_json_refuses_managed_paths(rel):
    logs = []
    with pytest.raises(shims.DirectJsonWriteRefused):
        shims.refuse_direct_json(rel, logger=logs.append)
    assert logs, "a refused direct-JSON write must be LOGGED"
    assert rel.split("/")[-1] in logs[0]


@pytest.mark.parametrize("rel", [
    "state/live-roster.json",
    "state/agent-state/gm.json",
    "state/agent-handoffs/gm.json",
    "state/protected-sessions.json",
])
def test_refuse_direct_json_allows_adjacent_paths(rel):
    # must NOT over-refuse files that are not one of the three managed projections
    shims.refuse_direct_json(rel, logger=lambda _m: None)


# --- DB shims write the STORE, never JSON ----------------------------------

def test_registry_update_writes_db_not_json(conn, tmp_path):
    _seed(conn)
    shims.registry_update(conn, ROOT, {"purpose": "new-purpose",
                                       "status": "parked"})
    assert conn.execute("SELECT purpose FROM lineages WHERE root=?",
                        (ROOT,)).fetchone()[0] == "new-purpose"
    assert conn.execute("SELECT status FROM canonical WHERE root=?",
                        (ROOT,)).fetchone()[0] == "parked"
    # wrote no JSON artifact
    assert not (tmp_path / "registry.json").exists()


def test_sessions_update_attributes_sid_via_db(conn):
    _seed(conn)
    shims.sessions_update(conn, ROOT, {"session_id": "sid-2"})
    gid = conn.execute("SELECT generation_id FROM canonical WHERE root=?",
                       (ROOT,)).fetchone()[0]
    assert conn.execute("SELECT session_id FROM generations WHERE id=?",
                        (gid,)).fetchone()[0] == "sid-2"


def test_sessions_update_sid_takes_over_stale_holder(conn):
    """The DB shim reuses the same-txn take-over: a sid held by a stale row is
    cleared (never a UNIQUE violation)."""
    _seed(conn)
    conn.execute("INSERT INTO lineages (root, tier, runtime) VALUES ('ghost','T2','claude')")
    ghost = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model) "
        "VALUES ('ghost', 1, 'sid-2', 'm')").lastrowid
    conn.commit()
    shims.sessions_update(conn, ROOT, {"session_id": "sid-2"})
    assert conn.execute("SELECT session_id FROM generations WHERE id=?",
                        (ghost,)).fetchone()[0] is None
