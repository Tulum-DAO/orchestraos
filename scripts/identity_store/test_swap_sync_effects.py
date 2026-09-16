"""F1 (RED) — G7 live finding: swaps.effects_status stays 'effects-incomplete' forever
on the rotate_agent SYNC path.

rotate_agent/promote run their OWN legacy effects choreography (tmux rename / archive /
kill — all execute synchronously in-process) but never call run_post_commit_effects
(the only thing that flips the label to 'complete'). So the sync swap row is labeled
'effects-incomplete' permanently. Benign for reads, but (a) any monitor keying on
incomplete-swaps false-flags forever, and (b) r-a-b's async resume-driver keys on EXACTLY
this label -> it would spuriously RE-RUN effects it does not own when its arm goes live.

FIX: the sync path records its OWN effects completion. execute_swap/swap_generation take
an opt-in flag (the sync caller owns+runs effects synchronously, so the async resume-driver
must hand off). DEFAULT preserves current behavior ('effects-incomplete') so r-a-b's async
arm + resume-driver contract are UNCHANGED (it still labels incomplete -> run_post_commit_
effects -> complete).

RED until execute_swap/swap_generation support marking the sync swap complete.
"""
import pytest

from scripts.identity_store import cutover, orchestra_db, identity_writer

ROOT = "orchestra-builder"


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "orchestra-registry.db"
    orchestra_db.init_db(str(p))
    c = orchestra_db.get_connection(str(p))
    c.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)",
              (ROOT, "T2", "claude"))
    blue = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                     "VALUES (?,5,'blue',?)", (ROOT, "m")).lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES (?,?,?,'online')", (ROOT, blue, ROOT))
    c.commit()
    yield c, blue
    c.close()


def _status(c, swap_id):
    return c.execute("SELECT effects_status FROM swaps WHERE id=?", (swap_id,)).fetchone()[0]


def test_sync_swap_marks_effects_complete(conn):
    """The SYNC path opts in -> the swap row is terminal ('complete'), so the async
    resume-driver hands off and monitors do not false-flag it forever."""
    c, blue = conn
    res = orchestra_db.execute_swap(
        c, ROOT, green={"generation": 6, "session_id": "g6", "model": "m"},
        blue_generation_id=blue, now="t2", sync_effects_owner=True)
    assert _status(c, res["swap_id"]) == "complete"


def test_async_default_stays_incomplete(conn):
    """DEFAULT (r-a-b async arm) is UNCHANGED: the swap stays 'effects-incomplete' so
    the resume-driver re-runs effects and run_post_commit_effects flips it complete."""
    c, blue = conn
    res = orchestra_db.execute_swap(
        c, ROOT, green={"generation": 6, "session_id": "g6", "model": "m"},
        blue_generation_id=blue, now="t2")
    assert _status(c, res["swap_id"]) == "effects-incomplete", \
        "r-a-b resume-driver contract must be unchanged by default"
    # the resume-driver path still works: running the effects flips it complete
    orchestra_db.run_post_commit_effects(c, res["swap_id"], effects=[])
    assert _status(c, res["swap_id"]) == "complete"


def test_swap_generation_sync_path_marks_complete(tmp_path):
    """End-to-end via the seam the sync path uses (identity_writer.swap_generation):
    under cutover, the sync path's swap row is 'complete' (not incomplete forever)."""
    orch = tmp_path
    (orch / "state").mkdir()
    dbp = orch / "state" / "orchestra-registry.db"
    orchestra_db.init_db(str(dbp))
    c = orchestra_db.get_connection(str(dbp))
    try:
        c.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)",
                  (ROOT, "T2", "claude"))
        blue = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                         "VALUES (?,5,'blue',?)", (ROOT, "m")).lastrowid
        c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
                  "VALUES (?,?,?,'online')", (ROOT, blue, ROOT))
        c.commit()
    finally:
        c.close()
    cutover.arm(str(orch))
    # the sync caller (_db_promote_swap) opts in via sync_effects_owner=True
    handled = identity_writer.swap_generation(
        str(orch), ROOT, green={"generation": 6, "session_id": "g6", "model": "m"},
        sync_effects_owner=True)
    assert handled is True
    c = orchestra_db.get_connection(str(dbp))
    try:
        row = c.execute("SELECT effects_status FROM swaps WHERE root=? "
                        "ORDER BY id DESC LIMIT 1", (ROOT,)).fetchone()
    finally:
        c.close()
    assert row["effects_status"] == "complete", \
        "F1: the sync-path swap must not stay effects-incomplete forever"
