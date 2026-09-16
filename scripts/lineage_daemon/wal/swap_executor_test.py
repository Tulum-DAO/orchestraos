"""RED-first tests for swap_executor (stage-3, DEC-1788323461 folds on the landed
identity store). The swap_executor WRAPS orchestra_db.execute_swap (the atomic
identity txn) + run_post_commit_effects (the idempotent external-effects step)
and adds the carried folds:

  - identity is atomic (store's job); a crash before COMMIT rolls back to Blue.
  - post-commit EFFECTS (tmux rename, grandchild-safe Blue reap) are idempotent
    and re-runnable; a crash mid-effects leaves swaps.effects_status
    ='effects-incomplete' (never an identity-reconcile state, U9).
  - #11 bounded wall-clock timeout on the effects section: a HUNG effect (e.g. a
    signal-ignoring reap) does NOT deadlock — on timeout the executor labels a
    DISTINCT half-swap outcome (effects-incomplete + swap-timeout reason) and
    returns for DEGRADED+page; identity stays coherent (already Green).
  - all external seams are dependency-INJECTED (spawn/verify/hydrate + each
    effect) so stage-3 uses pure fakes and stage-4 swaps in real ones (DP-U4).
"""
import sys
import time

import pytest

sys.path.insert(0, "scripts")
from identity_store.orchestra_db import (  # noqa: E402
    get_connection, init_db, execute_swap)
from lineage_daemon.wal.swap_executor import (  # noqa: E402
    SwapExecutor, SwapOutcome)


def _seeded_db(tmp_path):
    db = str(tmp_path / "reg.db")
    init_db(db)
    conn = get_connection(db)
    conn.execute("INSERT INTO lineages (root, tier, runtime) VALUES "
                 "('ios-watch-dev','T2','claude')")
    # Blue = gen6 canonical
    blue = conn.execute(
        "INSERT INTO generations (root, generation, model) "
        "VALUES ('ios-watch-dev', 6, 'claude-opus-4-8[1m]')").lastrowid
    conn.execute("INSERT INTO runtime_state (generation_id, status) "
                 "VALUES (?, 'online')", (blue,))
    conn.execute("INSERT INTO canonical (root, generation_id, tmux_session) "
                 "VALUES ('ios-watch-dev', ?, 'ios-watch-dev')", (blue,))
    conn.execute("COMMIT") if conn.in_transaction else None
    return db, conn, blue


def _green(gen=7):
    return {"generation": gen, "model": "claude-opus-4-8[1m]"}


def _canonical_gen(conn):
    return conn.execute(
        "SELECT g.generation FROM canonical c JOIN generations g "
        "ON c.generation_id=g.id WHERE c.root='ios-watch-dev'").fetchone()[0]


def test_successful_swap_repoints_and_runs_effects(tmp_path):
    db, conn, blue = _seeded_db(tmp_path)
    ran = []
    ex = SwapExecutor(db, effect_timeout_s=5.0)
    out = ex.swap("ios-watch-dev", _green(7), blue_generation_id=blue,
                  effects=[lambda: ran.append("rename"),
                           lambda: ran.append("reap")])
    assert out.status == "complete"
    assert _canonical_gen(conn) == 7          # identity moved to Green
    assert ran == ["rename", "reap"]          # effects ran in order
    # swaps row marked complete
    st = conn.execute("SELECT effects_status FROM swaps WHERE id=?",
                       (out.swap_id,)).fetchone()[0]
    assert st == "complete"


def test_crash_before_commit_rolls_back_to_blue(tmp_path):
    db, conn, blue = _seeded_db(tmp_path)
    ex = SwapExecutor(db)
    with pytest.raises(RuntimeError):
        ex.swap("ios-watch-dev", _green(7), blue_generation_id=blue,
                effects=[], _fail_swap_after=3)  # crash inside identity txn
    assert _canonical_gen(conn) == 6           # still Blue, zero repair
    assert conn.execute("SELECT COUNT(*) FROM swaps").fetchone()[0] == 0


def test_effects_crash_leaves_effects_incomplete_identity_green(tmp_path):
    db, conn, blue = _seeded_db(tmp_path)
    ex = SwapExecutor(db)

    def boom():
        raise RuntimeError("reap failed")

    out = ex.swap("ios-watch-dev", _green(7), blue_generation_id=blue,
                  effects=[boom])
    # identity is ALREADY Green (committed); effects incomplete, NOT reconcile
    assert _canonical_gen(conn) == 7
    assert out.status == "effects-incomplete"
    st = conn.execute("SELECT effects_status FROM swaps WHERE id=?",
                      (out.swap_id,)).fetchone()[0]
    assert st == "effects-incomplete"


def test_resume_effects_reruns_idempotently_to_complete(tmp_path):
    db, conn, blue = _seeded_db(tmp_path)
    ex = SwapExecutor(db)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first attempt fails")

    out = ex.swap("ios-watch-dev", _green(7), blue_generation_id=blue,
                  effects=[flaky])
    assert out.status == "effects-incomplete"
    # C2/#12 resume driver re-runs the effects for the stale swap
    out2 = ex.resume_effects(out.swap_id, effects=[flaky])
    assert out2.status == "complete"
    assert calls["n"] == 2


def test_hung_effect_times_out_distinct_label_not_deadlock(tmp_path):
    db, conn, blue = _seeded_db(tmp_path)
    ex = SwapExecutor(db, effect_timeout_s=0.3)

    def hang():
        time.sleep(5.0)  # signal-ignoring hung reap

    t0 = time.time()
    out = ex.swap("ios-watch-dev", _green(7), blue_generation_id=blue,
                  effects=[hang])
    elapsed = time.time() - t0
    assert elapsed < 3.0                       # did NOT wait the full 5s (no deadlock)
    assert out.status == "swap-timeout"        # DISTINCT half-swap label (#11)
    assert out.needs_degraded is True          # routes to DEGRADED+page
    # identity is coherent (Green); the timeout is an EFFECTS state, never identity
    assert _canonical_gen(conn) == 7
    st = conn.execute("SELECT effects_status FROM swaps WHERE id=?",
                      (out.swap_id,)).fetchone()[0]
    assert st == "effects-incomplete"


def test_timeout_state_is_distinct_from_ordinary_effects_incomplete(tmp_path):
    """gm GREEN criterion: a #11 timeout (half-swap, needs reap-COMPLETION) must
    be distinguishable from an ordinary effects-incomplete crash so the beat
    routes it to complete-the-swap, never to Green-spawn/prewarm."""
    db, conn, blue = _seeded_db(tmp_path)
    ex = SwapExecutor(db, effect_timeout_s=0.3)
    crash_out = ex.swap("ios-watch-dev", _green(7), blue_generation_id=blue,
                        effects=[lambda: (_ for _ in ()).throw(RuntimeError())])
    subdir = tmp_path / "b"
    subdir.mkdir()
    db2, conn2, blue2 = _seeded_db(subdir)
    ex2 = SwapExecutor(db2, effect_timeout_s=0.3)
    timeout_out = ex2.swap("ios-watch-dev", _green(7), blue_generation_id=blue2,
                          effects=[lambda: time.sleep(5.0)])
    assert crash_out.status == "effects-incomplete"
    assert timeout_out.status == "swap-timeout"
    assert crash_out.status != timeout_out.status
