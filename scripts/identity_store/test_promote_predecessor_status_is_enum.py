"""P0 guard (item b): promote's predecessor-archive status must be a valid enum value,
and the writer shim must REJECT a legacy literal (keeps the gate deliberate).

Regression: promote_successor.py wrote status='quiescent' for the archived predecessor;
after the item-(b) gate (shims.registry_update -> status_vocab.assert_valid) that literal
raised InvalidStatus and broke the NEXT rotation at promote. Fix: archived predecessor =
'parked' (no live process, resumable). This locks it.
"""
import pytest

from scripts.identity_store import orchestra_db, shims, status_vocab


def test_promote_predecessor_archive_status_is_enum():
    # the value promote_successor now writes for an archived predecessor
    assert status_vocab.assert_valid("parked") == "parked"
    # the OLD value must be rejected (proves the regression can't silently return)
    with pytest.raises(status_vocab.InvalidStatus):
        status_vocab.assert_valid("quiescent")


def test_registry_update_shim_rejects_legacy_status(tmp_path):
    db = tmp_path / "orchestra-registry.db"
    orchestra_db.init_db(str(db))
    conn = orchestra_db.get_connection(str(db))
    try:
        conn.execute("INSERT INTO lineages (root, tier, runtime, machine) "
                     "VALUES ('a1','T2','claude','vps')")
        conn.execute("INSERT INTO generations (root, generation, model) "
                     "VALUES ('a1',1,'m')")
        conn.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
                     "SELECT 'a1', id, 'a1', 'online' FROM generations WHERE root='a1'")
        conn.commit()
        # the gate refuses a legacy literal via the sanctioned mutable-status writer
        with pytest.raises(status_vocab.InvalidStatus):
            shims.registry_update(conn, "a1", {"status": "quiescent"})
        # an enum value is accepted
        shims.registry_update(conn, "a1", {"status": "parked"})
        row = conn.execute("SELECT status FROM canonical WHERE root='a1'").fetchone()
        assert row[0] == "parked"
    finally:
        conn.close()
