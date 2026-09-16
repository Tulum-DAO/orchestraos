"""RED (BG leg-(ii) P1.5 — build_obs carries Blue's idle state + age for the lull band).

collect nests Blue's activity as agent["death"]["state"] / ["state_age_s"] (collect.py),
but build_obs reduced death to a TOKEN and DROPPED both — so decide_bg had no idle signal to
cut over at a lull. P1.5: build_obs surfaces obs["state"] + obs["state_age_s"] (defaulting to
None/0) so the decide_bg lull band can fire. Drives the REAL build_obs.
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_beat  # noqa: E402


def _blue(gen=4):
    return {"generation": gen, "model": "claude-opus-4-8[1m]", "blue_generation_id": 41}


def _agent(state="idle", state_age_s=420):
    return {"agent_id": "second-brain-dev", "runtime": "claude",
            "ctx": {"status_bar_pct": 74},
            "death": {"state": state, "state_age_s": state_age_s}}


def test_build_obs_carries_state_and_age():
    obs = bg_beat.build_obs(_agent(state="idle", state_age_s=420), _blue())
    assert obs["state"] == "idle"
    assert obs["state_age_s"] == 420


def test_build_obs_state_age_defaults_zero_when_absent():
    obs = bg_beat.build_obs({"agent_id": "second-brain-dev", "runtime": "claude",
                             "ctx": {"status_bar_pct": 74}, "death": {}}, _blue())
    assert obs["state"] is None
    assert obs["state_age_s"] == 0
