"""RED tests for the per-provider discriminator profiles (B1 audit criteria #1 + #2).

Criterion #1 (SI independent audit): the codex-idle-baseline — UNMEASURED by A0 —
is actually CAPTURED here. Criterion #2: per-provider baseline+epsilon with the
bytes-axis PRIMARY is actually implemented (NOT a single global epsilon).

The A0 fixtures are the ground truth (contract/transcript/fixtures/<p>/). The
codex A0 fixture explicitly carries `idle_baseline_core_pct_UNMEASURED: true` —
these tests prove B1 closed that hole with a MEASURED, non-zero, band value.
"""
import json
import os

from lineage_daemon.realtime.provider_profiles import PROFILES, profile_for

_FIX = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                    "contract", "transcript", "fixtures")


def _a0(provider):
    with open(os.path.join(_FIX, provider, "a0-status-discriminator.json")) as fh:
        return json.load(fh)


# --- Audit criterion #1: codex-idle-baseline actually CAPTURED --------------

def test_codex_idle_baseline_captured_closing_the_a0_todo():
    # A0 left this UNMEASURED — prove the fixture still records the hole...
    a0 = _a0("codex")
    assert a0["discriminator"].get("idle_baseline_core_pct_UNMEASURED") is True
    # ...and prove B1 CLOSED it: a measured, non-zero render-loop baseline.
    p = PROFILES["codex"]
    assert p.idle_baseline_measured is True
    assert p.idle_baseline_core_pct > 0.0, (
        "codex idle is a live render loop, not ~0 like claude — a zero baseline "
        "would re-open the false-idle trap the A0 TODO flagged")
    # the measured band brackets the baseline (sustained 15-20s windows 3.3-5.2%,
    # bursty short windows up to ~9.5% — recorded from live codex-dev-1).
    lo, hi = p.idle_band_core_pct
    assert lo <= p.idle_baseline_core_pct <= hi
    assert lo >= 2.0 and hi >= 9.0, "codex idle band must reflect the measured burst ceiling"


def test_codex_cpu_axis_is_unreliable_bytes_and_io_primary():
    # THE codex finding: idle CPU band (2.4-9.5%) ENGULFS codex-compute (>=1.46%
    # per A0) — so CPU alone cannot separate idle from compute. bytes+IO primary.
    p = PROFILES["codex"]
    assert p.cpu_axis_reliable is False
    a0 = _a0("codex")
    compute_obs = a0["discriminator"]["computing_min_observed_core_pct"]  # 1.46
    assert compute_obs < p.idle_baseline_core_pct, (
        "codex-compute CPU sits BELOW idle-baseline CPU => CPU axis is confounded "
        "for codex; the IO-delta tiebreak is load-bearing, not decorative")


# --- Audit criterion #2: per-provider baseline+eps, NOT a single global eps --

def test_baselines_are_per_provider_distinct_not_a_single_global_epsilon():
    b = {k: PROFILES[k].idle_baseline_core_pct for k in ("claude", "codex", "gemini")}
    # NOT a single global value: gemini's render-loop floor (~1.4) differs from
    # the claude/codex heavy-render-loop floor (~4). Config also differs on
    # CPU-axis reliability: gemini's cpu axis is usable; claude's and codex's are
    # NOT (their 2.1.260 idle render loop engulfs compute).
    assert b["gemini"] < b["claude"] and b["gemini"] < b["codex"], f"baselines: {b}"
    assert PROFILES["gemini"].cpu_axis_reliable is True
    assert PROFILES["claude"].cpu_axis_reliable is False
    assert PROFILES["codex"].cpu_axis_reliable is False
    assert len({round(v, 1) for v in b.values()}) >= 2   # not one global value


def test_profiles_match_the_a0_measured_constants():
    # claude + gemini baselines/eps are pinned to the A0 fixture discriminators
    # (codex baseline is B1-measured; A0 left it UNMEASURED).
    # claude was RECALIBRATED for 2.1.260 (idle render loop 0 -> ~4%, cpu axis now
    # UNRELIABLE) so it no longer matches the pre-2.1.260 A0 baseline of 0 —
    # asserted against the 2.1.260 values, not A0.
    assert PROFILES["claude"].cpu_axis_reliable is False
    assert PROFILES["claude"].idle_baseline_core_pct >= 3.0
    ga = _a0("gemini")["discriminator"]
    assert PROFILES["gemini"].idle_baseline_core_pct == ga["idle_baseline_core_pct"]
    assert list(PROFILES["gemini"].idle_band_core_pct) == ga["idle_band_core_pct"]
    assert PROFILES["gemini"].smoothing == "median-of-3"  # single-window jitter


def test_gemini_idle_band_overlaps_codex_compute_the_reason_for_per_provider():
    # A0's load-bearing overlap: gemini-idle (<=1.95) overlaps codex-compute
    # (>=1.46). A single global CPU epsilon CANNOT separate them; per-provider
    # baselines + the IO axis is the ONLY sound resolution.
    g_hi = PROFILES["gemini"].idle_band_core_pct[1]     # 1.95
    x_compute = _a0("codex")["discriminator"]["computing_min_observed_core_pct"]  # 1.46
    assert x_compute < g_hi, "the overlap that forbids a single global epsilon"


def test_unknown_runtime_fails_safe_to_claude():
    # runtime_signatures precedent (the operator apr_a72c10f8): unknown -> claude.
    assert profile_for("nonesuch").provider == "claude"
    assert profile_for(None).provider == "claude"
    assert profile_for("codex").provider == "codex"
