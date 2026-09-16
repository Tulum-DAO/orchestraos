"""RED-first tests for decide_bg (stage-3): the Blue-Green transition decision.

Separate from the live decide.py (unchanged) — this is the prewarm/swap ladder
keyed on jsonl-token ctx truth from the WAL, emitting a REASON TOKEN that all
downstream gating keys on (never the action, the PLUG2 lesson).

Invariants:
  - death:* ALWAYS -> swap, dominates ctx, NEVER gated (death is never gated).
  - ctx≥0.80 -> swap; ctx≥0.70 -> prewarm; else noop.
  - UNCALIBRATED runtime (codex/gemini, no validated ceiling) -> pinned SOLO +
    alarm, NEVER prewarm/swap (fail-closed on the trigger).
  - reason token present + gating keys on reason, not action.
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal.decide_bg import decide_bg  # noqa: E402


def _obs(runtime="claude", ctx_pct=0.0, death=None, verified_ceiling=True):
    return {"root": "ios-watch-dev", "runtime": runtime, "ctx_pct": ctx_pct,
            "death": death, "ceiling_calibrated": verified_ceiling}


def test_noop_below_prewarm():
    d = decide_bg(_obs(ctx_pct=0.50))
    assert d["action"] == "noop"
    assert d["reason"].startswith("ctx:")


def test_prewarm_at_070():
    d = decide_bg(_obs(ctx_pct=0.72))
    assert d["action"] == "prewarm"
    assert d["reason"] == "ctx:prewarm"


def test_swap_at_080():
    d = decide_bg(_obs(ctx_pct=0.83))
    assert d["action"] == "swap"
    assert d["reason"] == "ctx:swap"


def test_death_always_swaps_dominating_low_ctx():
    d = decide_bg(_obs(ctx_pct=0.10, death="court"))
    assert d["action"] == "swap"
    assert d["reason"] == "death:court"
    assert d["never_gated"] is True


def test_death_swap_not_gated_even_for_t0():
    # death is never gated regardless of tier/ctx — the successor + WAL beats a
    # dead canonical.
    d = decide_bg(_obs(ctx_pct=0.0, death="oom"))
    assert d["action"] == "swap" and d["never_gated"] is True


def test_uncalibrated_runtime_pins_solo_alarm_not_swap():
    d = decide_bg(_obs(runtime="codex", ctx_pct=0.85, verified_ceiling=False))
    assert d["action"] == "noop"
    assert d["reason"] == "uncalibrated:solo-alarm"
    assert d["alarm"] is True


def test_uncalibrated_runtime_still_swaps_on_death():
    # fail-closed on the ctx TRIGGER, but death still dominates (a dead agent
    # must be rescued regardless of ceiling calibration).
    d = decide_bg(_obs(runtime="gemini", ctx_pct=0.0, death="wedged",
                       verified_ceiling=False))
    assert d["action"] == "swap"
    assert d["reason"] == "death:wedged"


def test_reason_token_present_for_all_actions():
    for obs in (_obs(ctx_pct=0.1), _obs(ctx_pct=0.72), _obs(ctx_pct=0.83),
                _obs(death="court")):
        assert decide_bg(obs)["reason"]
