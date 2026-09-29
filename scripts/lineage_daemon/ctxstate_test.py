from lineage_daemon.ctxstate import context_pct, tier, ceiling, effective_ceiling


# --- ceiling (raw model max — unchanged) ---

def test_ceiling_1m_model():
    assert ceiling("claude-opus-4-8[1m]") == 1_000_000

def test_ceiling_standard_model():
    assert ceiling("claude-opus-4") == 200_000


# --- effective_ceiling (auto-compact-aware; what the TUI bar measures) ---

def test_effective_ceiling_1m_is_800k():
    # calibrated: ob 690,334 tok -> 86%; ob-v2 342,488 -> 43%; both => ~800k
    assert effective_ceiling("claude-opus-4-8[1m]") == 800_000

def test_effective_ceiling_standard_reserves_proportionally():
    assert effective_ceiling("claude-opus-4") == 160_000

def test_effective_ceiling_matches_real_ob_bar():
    # 690,334 / 800,000 = 86.3% -> matches the human-visible 86% bar
    assert round(690334 / effective_ceiling("claude-opus-4-8[1m]") * 100) == 86


# --- context_pct ---

def test_status_bar_preferred_over_jsonl():
    obs = {"status_bar_pct": 42, "jsonl_tokens": 800000, "model": "claude-opus-4-8[1m]"}
    assert context_pct(obs) == 0.42

def test_status_bar_zero_still_preferred():
    obs = {"status_bar_pct": 0, "jsonl_tokens": 190000, "model": "claude-opus-4"}
    assert context_pct(obs) == 0.0

def test_jsonl_fallback_1m_model():
    obs = {"status_bar_pct": None, "jsonl_tokens": 800000, "model": "claude-opus-4-8[1m]"}
    assert context_pct(obs) == 0.8

def test_jsonl_fallback_standard_model_soft():
    obs = {"status_bar_pct": None, "jsonl_tokens": 160000, "model": "claude-opus-4"}
    assert context_pct(obs) == 0.8

def test_jsonl_fallback_standard_model_ok():
    obs = {"status_bar_pct": None, "jsonl_tokens": 30000, "model": "claude-opus-4"}
    assert context_pct(obs) == 0.15

def test_none_when_neither_present():
    obs = {"status_bar_pct": None, "jsonl_tokens": None, "model": "claude-opus-4"}
    assert context_pct(obs) is None


# --- tier ---

def test_tier_unknown():
    assert tier(None) == "unknown"

def test_tier_ok():
    assert tier(0.15) == "ok"

def test_tier_just_below_soft():
    assert tier(0.6999) == "ok"

def test_tier_soft_boundary():
    assert tier(0.70) == "SOFT"

def test_tier_soft_mid():
    assert tier(0.75) == "SOFT"

def test_tier_just_below_hard():
    assert tier(0.7999) == "SOFT"

def test_tier_hard_boundary():
    assert tier(0.80) == "HARD"

def test_tier_hard_above():
    assert tier(0.99) == "HARD"


# --- end-to-end tier via observations ---

def test_jsonl_1m_750k_is_soft():
    obs = {"status_bar_pct": None, "jsonl_tokens": 750000, "model": "claude-opus-4-8[1m]"}
    assert tier(context_pct(obs)) == "SOFT"

def test_jsonl_1m_800k_is_hard():
    obs = {"status_bar_pct": None, "jsonl_tokens": 800000, "model": "claude-opus-4-8[1m]"}
    assert tier(context_pct(obs)) == "HARD"

def test_neither_present_is_unknown():
    obs = {"status_bar_pct": None, "jsonl_tokens": None, "model": "x"}
    assert tier(context_pct(obs)) == "unknown"


# --- 2026-09-29: the "[1m]" marker convention was overtaken by reality -------
# Opus 5 / Sonnet 5 ship 1M NATIVELY with no marker, so the old rule measured
# every live seat against 200k. A seat at 942k tokens computed to ~589% full.

def test_1m_native_models_are_1m_without_the_marker():
    for m in ("claude-opus-5", "claude-sonnet-5", "claude-fable-5",
              "claude-opus-4-8", "claude-sonnet-4-6"):
        assert ceiling(m) == 1_000_000, f"{m} measured against the wrong window"

def test_longest_prefix_wins_so_opus_4_does_not_shadow_opus_4_8():
    assert ceiling("claude-opus-4-8") == 1_000_000
    assert ceiling("claude-opus-4") == 200_000

def test_200k_models_stay_200k():
    assert ceiling("claude-haiku-4-5") == 200_000
    assert ceiling("claude-sonnet-4-5") == 200_000

def test_bare_family_aliases_resolve():
    # registry.json records "opus"/"sonnet" for real seats; unresolved they
    # were the models being mis-measured.
    assert ceiling("opus") == 1_000_000
    assert ceiling("sonnet") == 1_000_000

def test_unknown_model_fails_closed_rather_than_guessing():
    # The whole point: an unrecognised model must yield NO OPINION. Returning a
    # default is what turned a stale rule into a confident wrong number.
    for m in ("some-future-model-9", "", None, "unknown", "  "):
        assert ceiling(m) is None, f"{m!r} silently got a default ceiling"
        assert effective_ceiling(m) is None

def test_context_pct_declines_to_guess_for_an_unknown_model():
    obs = {"jsonl_tokens": 500_000, "model": "some-future-model-9"}
    assert context_pct(obs) is None, "divided by a guessed ceiling"

def test_explicit_1m_marker_still_honoured():
    assert ceiling("claude-opus-4-8[1m]") == 1_000_000
