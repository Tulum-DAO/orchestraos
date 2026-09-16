from lineage_daemon.reconcile import (
    live_heads_by_name, single_live_head_violations,
    name_points_at_dead_session, heal_target, canonical_name,
)


def _rotated_gm():
    """gm rotated: gen-4 retired -> gen-5 live, both share lineage_root 'gm'."""
    return {
        "gm": {"tmux_session": "gm", "status": "retired",
               "succeeded_by": "gm-g5", "lineage_root": "gm"},
        "gm-g5": {"tmux_session": "gm-g5", "status": "active",
                  "lineage_root": "gm"},
    }


def test_canonical_name_uses_root():
    meta = _rotated_gm()
    assert canonical_name("gm-g5", meta) == "gm"
    assert canonical_name("solo-agent", {}) == "solo-agent"


def test_healthy_single_live_head_no_violation():
    meta = _rotated_gm()
    sessions = {"gm-g5"}  # only the successor is live
    assert single_live_head_violations(meta, sessions) == []
    assert live_heads_by_name(meta, sessions) == {"gm": ["gm-g5"]}


def test_name_pinned_to_dead_predecessor_heals_to_successor():
    meta = _rotated_gm()
    sessions = {"gm-g5"}                 # 'gm' session is dead
    assert name_points_at_dead_session("gm", meta, sessions) is True
    assert heal_target("gm", meta, sessions) == "gm-g5"


def test_no_live_head_is_a_violation_and_no_heal():
    meta = _rotated_gm()
    sessions = set()                     # whole lineage dead
    viols = single_live_head_violations(meta, sessions)
    assert [v["kind"] for v in viols] == ["no-live-head"]
    assert heal_target("gm", meta, sessions) is None


def test_jarvis_gm_name_collision_is_multiple_live_heads():
    """Stray relic 'jarvis-gm' sharing the gm root, both live + terminal =
    ambiguous. Reconciler must flag, never silently pick one."""
    meta = {
        "gm": {"tmux_session": "gm", "status": "active", "lineage_root": "gm"},
        "jarvis-gm": {"tmux_session": "jarvis-gm", "status": "active",
                      "lineage_root": "gm"},
    }
    sessions = {"gm", "jarvis-gm"}
    viols = single_live_head_violations(meta, sessions)
    assert len(viols) == 1
    assert viols[0]["kind"] == "multiple-live-heads"
    assert viols[0]["live_heads"] == ["gm", "jarvis-gm"]
    # heal refuses to pick under ambiguity
    assert heal_target("gm", meta, sessions) is None


def test_live_canonical_name_not_flagged_as_dead():
    meta = {"solo": {"tmux_session": "solo", "status": "active"}}
    assert name_points_at_dead_session("solo", meta, {"solo"}) is False
    assert single_live_head_violations(meta, {"solo"}) == []
