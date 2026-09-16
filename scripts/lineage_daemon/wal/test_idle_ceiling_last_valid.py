"""GOAL-FINDING #2 (idle-ceiling last-valid), GREEN-impl of the frozen-v2 conformance
intent — provider-agnostic, no runtime names.

An IDLE seat's ctx cannot have moved since its last render, so a RETAINED last-valid ctx
is VALID for an idle seat regardless of age. build_obs already retains last-valid (P0.6);
this finding makes calibration ACCEPT it when state=='idle' (both the lull band AND the
0.80 backstop) and suppress the blind-telemetry alarm — while a BUSY seat (ctx moving)
keeps the TTL fail-closed. Root cause of "idle at 84% never fired".
"""
import sys

sys.path.insert(0, "scripts")
from scripts.lineage_daemon.wal import bg_beat            # noqa: E402
from scripts.lineage_daemon.wal.decide_bg import decide_bg  # noqa: E402
from scripts.lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402

ROOT = "idle-ceiling-seat"


def _blue(sid="blue-sid"):
    return {"generation": 4, "model": "m", "blue_generation_id": 41, "session_id": sid}


def _agent(state, state_age_s):
    # no fresh ctx (no detector, no status_bar) -> build_obs falls to retained last-valid
    return {"agent_id": ROOT, "runtime": "claude", "ctx": {},
            "death": {"state": state, "state_age_s": state_age_s}}


def _obs_with_retained(tmp_path, state, state_age_s, retained=0.84, age=600.0):
    wal = str(tmp_path / "wal")
    now = 100000.0
    st = BgStateStore(wal, ROOT)
    st.write_meta("last_valid_ctx_pct", retained)
    st.write_meta("last_valid_ctx_ts", now - age)
    # DEC-1789517918317482 G1/G3: the lull swap is now guarded on attached/composer; this
    # fixture models a plain idle seat (nobody attached, nothing typed), so inject both
    # probes — with wal_dir set the live defaults would fail closed on a fake seat.
    return bg_beat.build_obs(_agent(state, state_age_s), _blue(), wal_dir=wal,
                             now=now, ctx_ttl_s=120.0, detector_dir=str(tmp_path / "det"),
                             attached_fn=lambda r: False, screen_fn=lambda r: ["❯ "])


def test_idle_seat_stale_retained_is_calibrated(tmp_path):
    obs = _obs_with_retained(tmp_path, "idle", 600, retained=0.84, age=600.0)
    assert obs["ctx_source"] == "stale"          # retained (no fresh read)
    assert obs["ctx_pct"] == 0.84
    assert obs["ceiling_calibrated"] is True, "idle + retained in-range ctx must calibrate"


def test_idle_seat_at_084_fires_swap_not_alarm(tmp_path):
    obs = _obs_with_retained(tmp_path, "idle", 600, retained=0.84, age=600.0)
    d = decide_bg(obs)
    assert d["action"] == "swap" and d["reason"] == "ctx:swap", d
    assert d["alarm"] is False, "an idle seat on a trusted retained value must NOT alarm"


def test_idle_seat_in_lull_band_accepts_retained(tmp_path):
    obs = _obs_with_retained(tmp_path, "idle", 600, retained=0.73, age=600.0)
    d = decide_bg(obs)
    # idle >= LULL_MIN_IDLE_S in [0.70,0.80) -> early lull swap on the retained value
    assert d["action"] == "swap" and d["reason"] == "ctx:lull-swap", d


def test_busy_seat_stale_retained_stays_failclosed(tmp_path):
    """A BUSY seat's ctx IS moving; a stale retained value must NOT calibrate (TTL
    rejection stays for busy) -> uncalibrated:solo-alarm, never a swap on a blind read."""
    obs = _obs_with_retained(tmp_path, "busy", 600, retained=0.84, age=600.0)
    assert obs["ceiling_calibrated"] is False
    d = decide_bg(obs)
    assert d["action"] == "noop" and d["reason"] == "uncalibrated:solo-alarm", d
