"""B5 REACHABLE-TRIGGER (gm PLAN GATE msg_0e400053): a TEST-ONLY, env-scoped threshold
override so a SUPERVISED autonomous beat can fire the REAL decide_bg decision path on a
low-ctx demo seat (0.30) — WITHOUT ever touching the fleet's core 0.70/0.80 constants.

INERT by default: absent env => core thresholds => production behavior unchanged.
The override lets ONE supervised beat prove the beat DECIDES + fires on its own on the REAL
(unforced) read, not a hand-fed obs.
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import decide_bg as db  # noqa: E402


def _obs(ctx):
    return {"root": "s", "runtime": "gemini", "ctx_pct": ctx, "ctx_unknown": False,
            "death": "", "state": "busy", "ceiling_calibrated": True,
            "blue_generation_id": 1, "green": {"generation": 2, "model": "m"}}


def test_core_constants_never_change():
    """HARD CONSTRAINT: the fleet's core thresholds stay 0.70/0.80 regardless of the env."""
    assert db.PREWARM_AT == 0.70 and db.SWAP_AT == 0.80


def test_inert_by_default_low_ctx_is_noop(monkeypatch):
    monkeypatch.delenv("BG_TEST_PREWARM_AT", raising=False)
    monkeypatch.delenv("BG_TEST_SWAP_AT", raising=False)
    d = db.decide_bg(_obs(0.30))
    assert d["action"] == "noop" and d["reason"] == "ctx:0.30", d  # core: 0.30 < 0.70


def test_env_override_fires_swap_on_low_ctx(monkeypatch):
    monkeypatch.setenv("BG_TEST_SWAP_AT", "0.28")   # 0.30 >= 0.28 => swap via REAL path
    d = db.decide_bg(_obs(0.30))
    assert d["action"] == "swap" and d["reason"] == "ctx:swap", d
    # core constants untouched even with the override active
    assert db.PREWARM_AT == 0.70 and db.SWAP_AT == 0.80


def test_env_override_fires_prewarm_band(monkeypatch):
    monkeypatch.setenv("BG_TEST_PREWARM_AT", "0.25")
    monkeypatch.setenv("BG_TEST_SWAP_AT", "0.90")   # 0.25 <= 0.30 < 0.90 => prewarm
    d = db.decide_bg(_obs(0.30))
    assert d["action"] == "prewarm" and d["reason"] == "ctx:prewarm", d


def test_malformed_env_falls_back_to_core(monkeypatch):
    monkeypatch.setenv("BG_TEST_SWAP_AT", "not-a-float")
    d = db.decide_bg(_obs(0.30))
    assert d["action"] == "noop", d  # bad override ignored -> core 0.80 -> 0.30 noop
