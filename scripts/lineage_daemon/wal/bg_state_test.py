"""RED-first tests for bg_state (stage-3): the per-lineage Blue-Green state
machine store + arming gates + the beat-as-swap-supervisor routing.

Load-bearing invariants (DEC-1788323461 + gm folds):
  - INERTNESS: with bg_enabled ABSENT (default) the whole machine is a no-op
    recorder — is_armed() is False and the supervisor takes no action (U10/#10).
  - GLOBAL kill-switch: state/wal/BG_DISABLED present ⇒ inert fleet-wide even if a
    per-lineage flag is set (r-a-b DP add).
  - state store: flock+temp+atomic writes (salvaged baseline_store discipline).
  - beat-as-supervisor on SWAPPING: lock HELD ⇒ strict no-op (#12); lock FREE ⇒
    re-invoke effects (C2/#13); swap-timeout ⇒ reap-COMPLETION route, never spawn.
  - DEGRADED ladder: re-page at N=4 beats, hard the operator-card at 2N=8; ctx≥0.90
    fast-path fires the card immediately; ladder resets on state transition.
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal.bg_state import (  # noqa: E402
    BgStateStore, is_armed, supervise, SUPERVISE_NOOP, SUPERVISE_RESUME_EFFECTS,
    SUPERVISE_COMPLETE_REAP, degraded_action)


# ---- arming / inertness ----

def test_disarmed_by_default(tmp_path):
    assert is_armed(str(tmp_path), "ios-watch-dev") is False


def test_armed_when_flag_file_present(tmp_path):
    (tmp_path / "ios-watch-dev.bg_enabled").write_text("")
    assert is_armed(str(tmp_path), "ios-watch-dev") is True


def test_global_disable_overrides_per_lineage_flag(tmp_path):
    (tmp_path / "ios-watch-dev.bg_enabled").write_text("")
    (tmp_path / "BG_DISABLED").write_text("")
    assert is_armed(str(tmp_path), "ios-watch-dev") is False


# ---- state store ----

def test_state_store_roundtrip_default_solo(tmp_path):
    st = BgStateStore(str(tmp_path), "ios-watch-dev")
    assert st.read()["state"] == "SOLO"
    st.write_state("PREWARMING", reason="ctx>=0.70")
    assert st.read()["state"] == "PREWARMING"
    assert st.read()["history"][-1]["reason"] == "ctx>=0.70"


def test_state_store_atomic_no_partial(tmp_path):
    st = BgStateStore(str(tmp_path), "ios-watch-dev")
    st.write_state("READY")
    import json
    # the file is valid json (temp+rename, never a partial write)
    p = tmp_path / "ios-watch-dev.bg.json"
    assert json.loads(p.read_text())["state"] == "READY"


# ---- beat-as-supervisor routing ----

def test_supervise_noop_when_not_swapping(tmp_path):
    st = BgStateStore(str(tmp_path), "ios-watch-dev")
    st.write_state("READY")
    assert supervise(st, lock_held=False, last_outcome=None) == SUPERVISE_NOOP


def test_supervise_swapping_lock_held_is_strict_noop(tmp_path):
    st = BgStateStore(str(tmp_path), "ios-watch-dev")
    st.write_state("SWAPPING")
    # executor alive (lock held) -> never double-swap (#12)
    assert supervise(st, lock_held=True, last_outcome=None) == SUPERVISE_NOOP


def test_supervise_swapping_lock_free_resumes_effects(tmp_path):
    st = BgStateStore(str(tmp_path), "ios-watch-dev")
    st.write_state("SWAPPING")
    # executor died (lock free), ordinary effects-incomplete -> re-invoke effects
    assert supervise(st, lock_held=False,
                     last_outcome="effects-incomplete") == SUPERVISE_RESUME_EFFECTS


def test_supervise_swap_timeout_routes_to_reap_completion_not_spawn(tmp_path):
    st = BgStateStore(str(tmp_path), "ios-watch-dev")
    st.write_state("SWAPPING")
    # #11 half-swap timeout -> reap-COMPLETION, NEVER Green-spawn (gm criterion)
    assert supervise(st, lock_held=False,
                     last_outcome="swap-timeout") == SUPERVISE_COMPLETE_REAP


# ---- DEGRADED ladder ----

def test_degraded_repage_at_n4():
    act = degraded_action(beats=4, ctx_pct=0.82)
    assert act["repage"] is True and act["shaw_card"] is False


def test_degraded_hard_card_at_2n8():
    act = degraded_action(beats=8, ctx_pct=0.82)
    assert act["shaw_card"] is True


def test_degraded_ctx_fastpath_fires_card_immediately():
    # ctx>=0.90 while DEGRADED -> the operator card NOW, skip the remaining window
    act = degraded_action(beats=1, ctx_pct=0.91)
    assert act["shaw_card"] is True
    assert act["reason"] == "ctx-pressure-fastpath"


def test_degraded_quiet_before_n4():
    act = degraded_action(beats=2, ctx_pct=0.82)
    assert act["repage"] is False and act["shaw_card"] is False


def test_telemetry_disable_is_distinct_from_arm_kill_and_arm_still_gated_by_bg():
    # DEC-1788479670: TELEMETRY_DISABLED and BG_DISABLED are DIFFERENT files with
    # opposite audiences. The arm lane MUST still be gated by BG_DISABLED (unchanged);
    # the new telemetry kill must NOT affect is_armed.
    import os
    import tempfile
    from lineage_daemon.wal import bg_state
    with tempfile.TemporaryDirectory() as wal:
        assert bg_state._telemetry_disable_path(wal) != bg_state._global_disable_path(wal)
        assert bg_state._telemetry_disable_path(wal).endswith("TELEMETRY_DISABLED")
        # arm a lineage, then prove BG_DISABLED (not TELEMETRY_DISABLED) disarms it
        open(bg_state._flag_path(wal, "root-x"), "w").close()
        assert bg_state.is_armed(wal, "root-x") is True
        open(bg_state._telemetry_disable_path(wal), "w").close()   # telemetry kill
        assert bg_state.is_armed(wal, "root-x") is True            # arm UNAFFECTED
        open(bg_state._global_disable_path(wal), "w").close()      # arm kill
        assert bg_state.is_armed(wal, "root-x") is False           # arm disarmed
