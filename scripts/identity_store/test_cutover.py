"""Phase-2 item-1 (RED) — cutover flag + flag-aware writer routing.

The rewire lands INERT: every rewired writer routes through ``identity_writer``,
which writes the DB ONLY when the cutover flag is active. The flag is OFF by
default (no flag file, env unset) so current strangler behavior is byte-identical
until the SEPARATE the operator-armed switch flips it. ``identity_writer`` ops return
False when inactive (caller falls back to its legacy JSON write) and True when
they handled the write against the DB.

RED until ``cutover`` / ``identity_writer`` exist.
"""
import os

import pytest

from scripts.identity_store import cutover, identity_writer, orchestra_db


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    return str(tmp_path)


def _seed(orchdir):
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root, tier, runtime, purpose) "
              "VALUES ('a1','T2','claude','old')")
    gid = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                    "VALUES ('a1',1,'s1','m')").lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES ('a1',?,'a1','online')", (gid,))
    c.execute("INSERT INTO runtime_state (generation_id, status, last_updated) "
              "VALUES (?,'online','t0')", (gid,))
    c.close()


def _db(orchdir):
    return orchestra_db.get_connection(
        os.path.join(orchdir, "state", "orchestra-registry.db"))


# --- flag ------------------------------------------------------------------

def test_inactive_by_default(orchdir):
    assert cutover.is_active(orchdir) is False


def test_arm_disarm_toggles_flag(orchdir):
    assert cutover.is_active(orchdir) is False
    cutover.arm(orchdir)
    assert cutover.is_active(orchdir) is True
    cutover.disarm(orchdir)
    assert cutover.is_active(orchdir) is False


def test_env_override_activates(orchdir, monkeypatch):
    monkeypatch.setenv("IDENTITY_STORE_CUTOVER", "1")
    assert cutover.is_active(orchdir) is True


# --- routing: INERT while inactive -----------------------------------------

def test_registry_writer_inactive_returns_false_and_leaves_db(orchdir):
    _seed(orchdir)
    assert identity_writer.update_registry_agent(orchdir, "a1", {"purpose": "new"}) is False
    c = _db(orchdir)
    assert c.execute("SELECT purpose FROM lineages WHERE root='a1'").fetchone()[0] == "old"
    c.close()


# --- routing: DB write while active ----------------------------------------

def test_registry_writer_active_writes_db(orchdir):
    _seed(orchdir)
    cutover.arm(orchdir)
    assert identity_writer.update_registry_agent(
        orchdir, "a1", {"purpose": "new", "status": "parked"}) is True
    c = _db(orchdir)
    assert c.execute("SELECT purpose FROM lineages WHERE root='a1'").fetchone()[0] == "new"
    assert c.execute("SELECT status FROM canonical WHERE root='a1'").fetchone()[0] == "parked"
    c.close()


def test_session_writer_active_writes_db(orchdir):
    _seed(orchdir)
    cutover.arm(orchdir)
    assert identity_writer.update_session(orchdir, "a1", {"session_id": "s2"}) is True
    c = _db(orchdir)
    gid = c.execute("SELECT generation_id FROM canonical WHERE root='a1'").fetchone()[0]
    assert c.execute("SELECT session_id FROM generations WHERE id=?", (gid,)).fetchone()[0] == "s2"
    c.close()


def test_state_writer_active_writes_runtime_state(orchdir):
    _seed(orchdir)
    cutover.arm(orchdir)
    assert identity_writer.write_agent_state(
        orchdir, "a1", {"status": "busy", "current_task": "x"}) is True
    c = _db(orchdir)
    gid = c.execute("SELECT generation_id FROM canonical WHERE root='a1'").fetchone()[0]
    row = c.execute("SELECT status, current_task FROM runtime_state "
                    "WHERE generation_id=?", (gid,)).fetchone()
    assert row["status"] == "busy" and row["current_task"] == "x"
    c.close()
