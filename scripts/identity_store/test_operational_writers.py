"""Phase-2 item-1 (RED) — operational-writer DB ops + the swap SEAM for r-a-b.

The 6 operational writers route through identity_writer when the cutover flag is
active. Two new ops beyond piece-2's shims:

* ``retire_agent`` — park-idle's retire in DB terms: drop the canonical pointer
  (the agent leaves the active roster, matching the legacy registry pop = the
  resurrection gate) while PRESERVING the generation row with retired_at set
  (resumable).
* ``swap_generation`` — the stable SEAM r-a-b's swap-path (promote_successor /
  rotate_agent) calls, so the swap activates through the SAME cutover flag +
  barrier as everything else (one coherent activation, no racing flags). Wraps
  ``orchestra_db.execute_swap``.

RED until those ops exist.
"""
import os

import pytest

from scripts.identity_store import cutover, identity_writer, orchestra_db

ROOT = "a1"


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    return str(tmp_path)


def _seed(orchdir):
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)",
              (ROOT, "T2", "claude"))
    gid = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                    "VALUES (?,?,?,?)", (ROOT, 1, "s1", "m")).lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES (?,?,?,'online')", (ROOT, gid, ROOT))
    c.execute("INSERT INTO runtime_state (generation_id, status, last_updated) "
              "VALUES (?,'online','t0')", (gid,))
    c.close()
    return gid


def _db(orchdir):
    return orchestra_db.get_connection(
        os.path.join(orchdir, "state", "orchestra-registry.db"))


# --- retire_agent ----------------------------------------------------------

def test_retire_agent_inactive_returns_false(orchdir):
    _seed(orchdir)
    assert identity_writer.retire_agent(orchdir, ROOT, reason="idle") is False


def test_retire_agent_active_drops_canonical_preserves_generation(orchdir):
    gid = _seed(orchdir)
    cutover.arm(orchdir)
    assert identity_writer.retire_agent(orchdir, ROOT, reason="idle") is True
    c = _db(orchdir)
    # left the active roster (canonical pop = the legacy resurrection gate)
    assert c.execute("SELECT 1 FROM canonical WHERE root=?", (ROOT,)).fetchone() is None
    # generation preserved with retired_at set (resumable)
    row = c.execute("SELECT retired_at FROM generations WHERE id=?", (gid,)).fetchone()
    assert row is not None and row["retired_at"] is not None
    c.close()


# --- swap_generation (the seam r-a-b calls) --------------------------------

def test_swap_generation_inactive_returns_false(orchdir):
    gid = _seed(orchdir)
    assert identity_writer.swap_generation(
        orchdir, ROOT,
        green={"generation": 2, "session_id": "s2", "model": "m"},
        blue_generation_id=gid) is False


def test_swap_generation_active_runs_execute_swap(orchdir):
    gid = _seed(orchdir)
    cutover.arm(orchdir)
    res = identity_writer.swap_generation(
        orchdir, ROOT,
        green={"generation": 2, "session_id": "s2", "model": "m"},
        blue_generation_id=gid, now="t1")
    assert res is True
    c = _db(orchdir)
    row = c.execute("SELECT g.generation, g.session_id FROM canonical c "
                    "JOIN generations g ON g.id=c.generation_id WHERE c.root=?",
                    (ROOT,)).fetchone()
    assert row["generation"] == 2 and row["session_id"] == "s2"
    blue = c.execute("SELECT retired_at FROM generations WHERE id=?", (gid,)).fetchone()
    assert blue["retired_at"] == "t1"
    c.close()


def test_swap_generation_resolves_blue_from_canonical(orchdir):
    """A caller (promote_successor) need not resolve blue itself — swap_generation
    resolves blue_generation_id from the current canonical pointer."""
    gid = _seed(orchdir)
    cutover.arm(orchdir)
    res = identity_writer.swap_generation(
        orchdir, ROOT,
        green={"generation": 2, "session_id": "s2", "model": "m"},
        blue_generation_id=None, now="t1")   # <-- not supplied
    assert res is True
    c = _db(orchdir)
    assert c.execute("SELECT retired_at FROM generations WHERE id=?",
                     (gid,)).fetchone()["retired_at"] == "t1"
    c.close()


def test_swap_generation_fail_closed_when_lineage_absent(orchdir):
    """r-a-b precondition (folded): under cutover a manual rotation for an
    unknown root must FAIL-CLOSED, never fall back to writing identity around the
    store."""
    _seed(orchdir)
    cutover.arm(orchdir)
    with pytest.raises(identity_writer.SwapPreconditionError):
        identity_writer.swap_generation(
            orchdir, "no-such-root",
            green={"generation": 2, "session_id": "sX", "model": "m"},
            blue_generation_id=None)
