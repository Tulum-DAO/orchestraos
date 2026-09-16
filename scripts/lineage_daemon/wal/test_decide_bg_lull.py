"""RED (BG leg-(ii) P1.5 — lull band: swap EARLY at an idle lull in [PREWARM, SWAP)).

decide_bg was a pure ctx/death ladder that only swapped at the hard SWAP_AT=0.80 wall — which
can fire MID-TURN and lose Blue's in-flight (WAL-uncommitted) work on reap. the operator contract #4:
cut over at an idle LULL in 70-80%, not only the 80% wall. P1.5: within [PREWARM_AT, SWAP_AT),
if Blue is idle (state=="idle") and has been idle >= LULL_MIN_IDLE_S, decide a swap with
reason ctx:lull-swap. The hard 0.80 backstop is retained. This composes with P0.5's READY-gate
downstream (never_gated=False), so a lull-swap only FIRES when the Green is verified-READY —
else it defers to prewarm/verify.
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal.decide_bg import decide_bg, LULL_MIN_IDLE_S  # noqa: E402


def _obs(ctx_pct, state=None, state_age_s=0, death=None):
    return {"root": "x", "runtime": "claude", "death": death,
            "ceiling_calibrated": True, "ctx_pct": ctx_pct,
            "state": state, "state_age_s": state_age_s}


# ── lull-swap fires in the band when Blue is idle long enough ──────────────────

def test_lull_swap_fires_when_idle_in_band():
    d = decide_bg(_obs(0.74, state="idle", state_age_s=LULL_MIN_IDLE_S))
    assert d["action"] == "swap"
    assert d["reason"] == "ctx:lull-swap"
    assert d["never_gated"] is False, "a lull-swap is READY-gated by P0.5 downstream"


# ── no lull-swap while Blue is busy or freshly idle => prewarm ─────────────────

def test_no_lull_swap_when_busy():
    d = decide_bg(_obs(0.74, state="busy", state_age_s=9999))
    assert d["action"] == "prewarm"
    assert d["reason"] == "ctx:prewarm"


def test_no_lull_swap_when_idle_but_too_fresh():
    d = decide_bg(_obs(0.74, state="idle", state_age_s=LULL_MIN_IDLE_S - 1))
    assert d["action"] == "prewarm"
    assert d["reason"] == "ctx:prewarm"


def test_no_lull_swap_when_state_missing():
    d = decide_bg(_obs(0.74, state=None, state_age_s=0))
    assert d["action"] == "prewarm"


# ── hard 0.80 backstop retained (swaps regardless of idle) ─────────────────────

def test_hard_backstop_swaps_at_080_even_if_busy():
    d = decide_bg(_obs(0.83, state="busy", state_age_s=0))
    assert d["action"] == "swap"
    assert d["reason"] == "ctx:swap"


def test_below_prewarm_is_noop_even_if_idle():
    d = decide_bg(_obs(0.50, state="idle", state_age_s=9999))
    assert d["action"] == "noop"


# ── death still dominates the whole ladder ─────────────────────────────────────

def test_death_dominates_over_lull():
    d = decide_bg(_obs(0.74, state="idle", state_age_s=9999, death="oom"))
    assert d["action"] == "swap"
    assert d["reason"] == "death:oom"
    assert d["never_gated"] is True
