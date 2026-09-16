"""Piece-2 (RED) — U9: post-commit effects are labeled, never reconcile identity.

Identity is coherent from COMMIT onward BY CONSTRUCTION (the DB says Green). The
external effects that follow a swap (tmux rename, kill Blue pane, WAL markers)
are NOT identity — they are idempotent, retryable, ordered side-effects. U9:

* a swap records its effects as ``effects-incomplete`` at COMMIT;
* if an effect fails, the label STAYS ``effects-incomplete`` (a resume driver
  re-runs the effects) and the DB identity NEVER regresses — there is no
  identity-reconcile state, ever;
* on success the label becomes ``complete``; re-running is idempotent.

RED until ``run_post_commit_effects`` exists.
"""
import pytest

from scripts.identity_store import orchestra_db

ROOT = "orchestra-builder"


@pytest.fixture
def swapped(tmp_path):
    """A committed swap: canonical already points to Green, effects pending."""
    p = tmp_path / "orchestra-registry.db"
    orchestra_db.init_db(str(p))
    conn = orchestra_db.get_connection(str(p))
    conn.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)",
                 (ROOT, "T2", "claude"))
    blue_id = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model) "
        "VALUES (?,?,?,?)", (ROOT, 1, "blue-sid", "m")).lastrowid
    conn.execute(
        "INSERT INTO canonical (root, generation_id, tmux_session, status) "
        "VALUES (?,?,?,?)", (ROOT, blue_id, ROOT, "online"))
    conn.commit()
    res = orchestra_db.execute_swap(
        conn, ROOT,
        green={"generation": 2, "session_id": "green-sid", "model": "model-green"},
        blue_generation_id=blue_id, now="t1")
    yield conn, res["swap_id"], res["green_generation_id"]
    conn.close()


def _effects_status(conn, swap_id):
    return conn.execute("SELECT effects_status FROM swaps WHERE id=?",
                        (swap_id,)).fetchone()[0]


def _canonical_gen(conn):
    return conn.execute("SELECT generation_id FROM canonical WHERE root=?",
                        (ROOT,)).fetchone()[0]


def test_swap_records_effects_incomplete_and_identity_coherent(swapped):
    conn, swap_id, green_id = swapped
    assert _effects_status(conn, swap_id) == "effects-incomplete"
    assert _canonical_gen(conn) == green_id


def test_effect_failure_keeps_incomplete_and_never_reconciles_identity(swapped):
    conn, swap_id, green_id = swapped

    def good():
        pass

    def boom():
        raise RuntimeError("tmux rename failed")

    with pytest.raises(RuntimeError):
        orchestra_db.run_post_commit_effects(conn, swap_id, [good, boom])

    # effects stay incomplete -> re-runnable by the resume driver
    assert _effects_status(conn, swap_id) == "effects-incomplete"
    # IDENTITY NEVER REGRESSES: canonical still Green, no reconcile state exists
    assert _canonical_gen(conn) == green_id


def test_effects_complete_on_success_and_idempotent(swapped):
    conn, swap_id, green_id = swapped
    calls = {"n": 0}

    def good():
        calls["n"] += 1

    orchestra_db.run_post_commit_effects(conn, swap_id, [good, good])
    assert _effects_status(conn, swap_id) == "complete"
    assert calls["n"] == 2

    # idempotent re-run: stays complete, identity unchanged
    orchestra_db.run_post_commit_effects(conn, swap_id, [good, good])
    assert _effects_status(conn, swap_id) == "complete"
    assert _canonical_gen(conn) == green_id
