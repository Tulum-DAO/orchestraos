"""Phase-2 item-4 (RED) — cutover rollback restores the strangler default.

The rollback is deliberately trivial because the strangler keeps the legacy JSON
path fully current: disarm the cutover flag (writers go straight back to direct
JSON), disarm the liveness monitor, and lift any freeze. After rollback the
system is byte-for-byte in its pre-cutover state — no data migration to undo.

RED until ``cutover.rollback`` exists.
"""
import os

import pytest

from scripts.identity_store import cutover, freeze, identity_writer, monitor, orchestra_db


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
    c.close()


def test_rollback_restores_strangler_default(orchdir):
    cutover.arm(orchdir)
    monitor.arm(orchdir)
    freeze.freeze(orchdir)

    cutover.rollback(orchdir)

    assert cutover.is_active(orchdir) is False
    assert monitor.is_armed(orchdir) is False
    assert freeze.is_frozen(orchdir) is False


def test_rollback_reverts_writers_to_legacy_json(orchdir):
    _seed(orchdir)
    cutover.arm(orchdir)
    # active: identity_writer handles against the DB
    assert identity_writer.update_registry_agent(orchdir, "a1", {"purpose": "x"}) is True

    cutover.rollback(orchdir)

    # rolled back: writer routing returns False => caller does its legacy JSON write
    assert identity_writer.update_registry_agent(orchdir, "a1", {"purpose": "y"}) is False


def test_rollback_is_idempotent(orchdir):
    cutover.rollback(orchdir)   # nothing armed — must not raise
    cutover.rollback(orchdir)
    assert cutover.is_active(orchdir) is False
