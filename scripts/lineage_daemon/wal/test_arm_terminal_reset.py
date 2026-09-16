"""DEFECT (gm msg_96438e1d, verified by effect): an ARMED seat whose bg.json holds a STALE
TERMINAL state (DRAINED / DEGRADED / RETIRE_PENDING) from a prior fire is EXCLUDED FOREVER —
the beat logs 'DRAINED->DRAINED reason=swap-complete' then 'mode=ARMED -> skip:excluded' every
tick, and b_fire_watcher pages gm for a non-transition. A terminal state can never be carried
into a fresh arm: arm_lineage must reset it to SOLO (post-conformance-gate, pre-hooks/flag)
so a re-armed seat is fire-able again, while an in-flight (SOLO/PREWARMING/READY/SWAPPING) seat
is left untouched (never reset a live fire)."""
import json
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_state  # noqa: E402

ROOT = "second-brain-dev"


def _detector(tmp_path, sid, used_pct=50, ts=1_000_000.0):
    (tmp_path / f"claude-ctx-{sid}.json").write_text(
        json.dumps({"used_pct": used_pct, "timestamp": ts}))


def _seed_bg_json(wal_dir, root, state, history, meta=None):
    store = bg_state.BgStateStore(str(wal_dir), root)
    data = {"state": state, "root": root, "history": history,
            "meta": meta or {}}
    store._atomic_write(data)
    return store


def _arm(tmp_path, root="second-brain-dev"):
    wal = str(tmp_path)
    _detector(tmp_path, "sidX")
    bg_state.arm_lineage(wal, root, "claude", seat=root,
                         detector_dir=wal, sid="sidX", now=1_000_000.0, ttl_s=120.0)
    return bg_state.BgStateStore(wal, root).read()


def test_drained_terminal_reset_to_solo(tmp_path):
    history = [{"state": "READY", "reason": None},
               {"state": "SWAPPING", "reason": "ctx-fastpath"},
               {"state": "DEGRADED", "reason": "verify-stall"},
               {"state": "DRAINED", "reason": "swap-complete"}]
    _seed_bg_json(tmp_path, ROOT, "DRAINED", history,
                  meta={"last_valid_ctx_pct": 0.42, "last_valid_ctx_ts": 999.0})
    data = _arm(tmp_path)
    assert data["state"] == "SOLO"
    assert data["history"][-1]["state"] == "SOLO"
    assert "arm:terminal-reset" in (data["history"][-1].get("reason") or "")
    # meta preserved, not wiped
    assert data["meta"]["last_valid_ctx_pct"] == 0.42
    assert data["meta"]["last_valid_ctx_ts"] == 999.0


def test_degraded_terminal_reset_to_solo(tmp_path):
    history = [{"state": "READY", "reason": None},
               {"state": "SWAPPING", "reason": "ctx-fastpath"},
               {"state": "DEGRADED", "reason": "verify-stall"}]
    _seed_bg_json(tmp_path, ROOT, "DEGRADED", history,
                  meta={"last_valid_ctx_pct": 0.77})
    data = _arm(tmp_path)
    assert data["state"] == "SOLO"
    assert data["history"][-1]["state"] == "SOLO"
    assert "arm:terminal-reset" in (data["history"][-1].get("reason") or "")
    assert data["meta"]["last_valid_ctx_pct"] == 0.77


def test_retire_pending_terminal_reset_to_solo(tmp_path):
    history = [{"state": "SWAPPING", "reason": "ctx-fastpath"},
               {"state": "RETIRE_PENDING", "reason": "reap-refused"}]
    _seed_bg_json(tmp_path, ROOT, "RETIRE_PENDING", history,
                  meta={"blue_pid": 4242})
    data = _arm(tmp_path)
    assert data["state"] == "SOLO"
    assert data["history"][-1]["state"] == "SOLO"
    assert "arm:terminal-reset" in (data["history"][-1].get("reason") or "")
    assert data["meta"]["blue_pid"] == 4242


def test_live_inflight_state_not_touched(tmp_path):
    for live_state in ("SOLO", "PREWARMING", "READY", "SWAPPING"):
        root = f"live-{live_state.lower()}"
        history = [{"state": live_state, "reason": "in-flight"}]
        _seed_bg_json(tmp_path, root, live_state, history)
        data = _arm(tmp_path, root=root)
        assert data["state"] == live_state
        # no reset entry appended: history unchanged (same length, same last reason)
        assert data["history"] == history


def test_no_bg_json_unchanged_behavior_no_crash(tmp_path):
    wal = str(tmp_path)
    root = "no-bg-json-seat"
    _detector(tmp_path, "sidY")
    # no bg.json seeded at all — arm_lineage must not crash
    path = bg_state.arm_lineage(wal, root, "claude", seat=root,
                                detector_dir=wal, sid="sidY", now=1_000_000.0, ttl_s=120.0)
    assert path.endswith(f"{root}.bg_enabled")
    data = bg_state.BgStateStore(wal, root).read()
    assert data["state"] == "SOLO"  # default, unreset (nothing to reset)
