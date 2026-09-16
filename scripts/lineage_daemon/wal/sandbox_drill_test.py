"""Sandbox swap DRILL (stage-3, DP-S3-4): the async arm driven end-to-end against
a REAL identity store (orchestra_db, present on this base) through a realistic
swap_fn, proving the full PREWARMING->READY->SWAPPING->DRAINED lifecycle lands a
COHERENT SINGLE canonical head — not just fakes agreeing with fakes.

Pane spawn/verify/hydrate/reap stay pure fakes (a real pane is the stage-4 sandbox
drill); the SWAP itself hits the real execute_swap so the drill proves the arm's
orchestration composes with the atomic store: one canonical row, Blue retired,
swaps row labeled, projection-coherent by construction.
"""
import sys

sys.path.insert(0, "scripts")
from identity_store.orchestra_db import (  # noqa: E402
    get_connection, init_db, execute_swap)
from lineage_daemon.wal.bg_arm import BgArm  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402


def _seed_store(tmp_path):
    db = str(tmp_path / "orchestra-registry.db")
    init_db(db)
    conn = get_connection(db)
    conn.execute("INSERT INTO lineages (root, tier, runtime) VALUES "
                 "('ios-watch-dev','T2','claude')")
    blue = conn.execute(
        "INSERT INTO generations (root, generation, model) "
        "VALUES ('ios-watch-dev', 6, 'claude-opus-4-8[1m]')").lastrowid
    conn.execute("INSERT INTO runtime_state (generation_id, status) "
                 "VALUES (?, 'online')", (blue,))
    conn.execute("INSERT INTO canonical (root, generation_id, tmux_session) "
                 "VALUES ('ios-watch-dev', ?, 'ios-watch-dev')", (blue,))
    conn.close()
    return db, blue


class _RealSwapSeams:
    """Fakes for pane effects; a REAL execute_swap for the identity step (the
    stage-4 swap_fn wraps swap_generation which wraps execute_swap — same core)."""
    def __init__(self, db):
        self._db = db
        self.calls = []

    def spawn(self, root, alias):
        self.calls.append("spawn")
        return {"alias": alias}

    def register_provisional(self, root, alias, generation=None, model=None):
        self.calls.append("register_provisional")

    def project_now(self, root):
        self.calls.append("project_now")   # F3 read-your-writes (fake: no-op)

    def verify(self, root, alias):
        self.calls.append("verify")
        return True

    def hydrate(self, root, alias, since_seq):
        self.calls.append("hydrate")

    def swap(self, root, green, blue_generation_id):
        self.calls.append("swap")
        conn = get_connection(self._db)
        try:
            res = execute_swap(conn, root, green,
                               blue_generation_id=blue_generation_id)
        finally:
            conn.close()
        return type("O", (), {"status": "complete", "swap_id": res["swap_id"],
                              "green_generation_id": res["green_generation_id"],
                              "needs_degraded": False})()

    def reap(self, root, blue):
        self.calls.append("reap")


def _obs(ctx_pct=0.0, death=None, blue=6):
    return {"root": "ios-watch-dev", "runtime": "claude", "ctx_pct": ctx_pct,
            "death": death, "ceiling_calibrated": True,
            "blue_generation_id": blue,
            "green": {"generation": 7, "model": "claude-opus-4-8[1m]"}}


def _canonical(db):
    conn = get_connection(db)
    try:
        row = conn.execute(
            "SELECT g.generation, g.retired_at FROM canonical c "
            "JOIN generations g ON c.generation_id=g.id "
            "WHERE c.root='ios-watch-dev'").fetchone()
        heads = conn.execute("SELECT COUNT(*) FROM canonical "
                             "WHERE root='ios-watch-dev'").fetchone()[0]
        blue_retired = conn.execute(
            "SELECT retired_at FROM generations WHERE root='ios-watch-dev' "
            "AND generation=6").fetchone()[0]
        return {"canonical_gen": row[0], "heads": heads,
                "blue_retired": blue_retired is not None}
    finally:
        conn.close()


def test_full_lifecycle_lands_coherent_single_head(tmp_path):
    db, blue = _seed_store(tmp_path)
    (tmp_path / "ios-watch-dev.bg_enabled").write_text("")
    seams = _RealSwapSeams(db)
    arm = BgArm(str(tmp_path), "ios-watch-dev", seams=seams, blue_wal_event_count_fn=lambda: 1,
                cutover_active=lambda: True)

    arm.beat(_obs(ctx_pct=0.72, blue=blue))   # PREWARMING
    arm.beat(_obs(ctx_pct=0.74, blue=blue))   # READY
    arm.beat(_obs(ctx_pct=0.83, blue=blue))   # SWAP -> DRAINED

    # lifecycle ran in order — F3 read-your-writes: project_now between register+spawn
    assert seams.calls[:3] == ["register_provisional", "project_now", "spawn"]
    assert "verify" in seams.calls and "swap" in seams.calls and "reap" in seams.calls
    assert BgStateStore(str(tmp_path), "ios-watch-dev").read()["state"] == "DRAINED"

    # THE COHERENCE ASSERTION: exactly one canonical head = Green(7), Blue retired
    c = _canonical(db)
    assert c["heads"] == 1
    assert c["canonical_gen"] == 7
    assert c["blue_retired"] is True


def test_death_path_cold_spawn_then_swap_coherent(tmp_path):
    db, blue = _seed_store(tmp_path)
    (tmp_path / "ios-watch-dev.bg_enabled").write_text("")
    seams = _RealSwapSeams(db)
    arm = BgArm(str(tmp_path), "ios-watch-dev", seams=seams, blue_wal_event_count_fn=lambda: 1,
                cutover_active=lambda: True)
    # death straight from SOLO -> cold spawn + immediate swap
    arm.beat(_obs(ctx_pct=0.05, death="oom", blue=blue))
    assert "spawn" in seams.calls and "swap" in seams.calls
    c = _canonical(db)
    assert c["heads"] == 1 and c["canonical_gen"] == 7 and c["blue_retired"]
