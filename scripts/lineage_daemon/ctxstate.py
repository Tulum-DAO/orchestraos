"""Two-tier, model-ceiling-aware context classifier (pure).

Context % is derived from either the TUI status-bar % (already
model-ceiling-aware) or, as a fallback, observed jsonl token count
divided by the model's context ceiling. The fleet mixes [1m] and
standard model variants, so raw token counts alone are ambiguous.
"""

from typing import Optional


# Codex windows, DERIVED from the authoritative session metadata (never
# guessed — same law as the unknown-model refusal in collect.py):
# codex_context.get_codex_session_context('gpt-6-astra-agent') 2026-09-15 ->
# (61.35%, 158,541 used, 258,400 model_context_window). Codex accounting has
# no auto-compact reserve — its used_pct divides by the FULL window — so
# effective_ceiling returns these raw (see _is_codex_window_model).
_GPT6_ASTRA_WINDOW = 258_400


def _is_codex_window_model(m: str) -> bool:
    return "gpt-6-astra" in m


# Claude context windows, keyed by model id. CACHED, NOT LIVE — taken from the
# bundled claude-api skill's model table, cached 2026-06-24. The authoritative
# source is the Models API `max_input_tokens` field
# (client.models.retrieve("<id>")); refresh from there whenever credentials
# exist on the host. There were none when this was written.
#
# WHY THIS TABLE EXISTS AT ALL: the previous rule returned 1M only when the
# model string literally contained "[1m]". That was true when 1M was an opt-in
# variant, and it silently stopped being true when Opus 5 / Sonnet 5 shipped 1M
# NATIVELY with no marker. Live seats carry "claude-sonnet-5", "opus" and
# "sonnet" — none matched — so every seat was measured against 200k while
# actually running a 1M window, a 6.25x under-estimate. A seat at 942k tokens
# computed to ~589% "full". Nothing failed loudly; it just quietly lied.
#
# Longest-prefix wins, so "claude-opus-4-8" is not shadowed by "claude-opus-4".
_CLAUDE_WINDOWS = {
    # 1M-native generation
    "claude-fable-5": 1_000_000,
    "claude-mythos-5": 1_000_000,
    "claude-opus-5": 1_000_000,
    "claude-opus-4-8": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    # 200k generation
    "claude-haiku-4-5": 200_000,
    "claude-opus-4-5": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-opus-4": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-haiku-4": 200_000,
}

# Bare aliases the fleet actually records in registry.json ("opus", "sonnet").
# They resolve to whatever that family's CURRENT model is, so they are dated by
# nature: as of 2026-06-24 both point at 1M-native models. This is the same
# shape of assumption that the "[1m]" marker made, so it is written down
# explicitly rather than buried in a regex — re-check it when a family's
# default window changes.
_CLAUDE_ALIASES = {"opus": 1_000_000, "sonnet": 1_000_000, "haiku": 200_000}


def ceiling(model: str) -> Optional[int]:
    """RAW context-window ceiling in tokens, or None when the model is unknown.

    FAILS CLOSED. An unrecognised model returns None, and every caller must
    treat None as "no opinion" rather than substituting a default. The previous
    behaviour — silently returning 200_000 for anything unmatched — is what
    turned an out-of-date rule into a confident wrong number instead of an
    absence. An unassessed seat is recoverable; a seat wrongly measured at 589%
    full would have been rotated.
    """
    m = (model or "").lower().strip()
    if not m or m == "unknown":
        return None
    if _is_codex_window_model(m):
        return _GPT6_ASTRA_WINDOW
    # Explicit opt-in marker and the non-Claude runtimes keep their old handling.
    if "[1m]" in m or "gemini" in m or "flash" in m or "pro" in m or "agy" in m or "antigravity" in m:
        return 1_000_000
    for prefix in sorted(_CLAUDE_WINDOWS, key=len, reverse=True):
        if m.startswith(prefix):
            return _CLAUDE_WINDOWS[prefix]
    if m in _CLAUDE_ALIASES:
        return _CLAUDE_ALIASES[m]
    return None


# The TUI context bar (and auto-compact) does NOT count against the raw ceiling
# -- Claude Code reserves headroom for the response/system, so the bar reads
# higher than tokens/raw_ceiling. Calibrated against two live [1m] sessions on
# 2026-08-12: orchestra-builder 690,334 tok -> bar 86% (=> ~802,714) and
# orchestra-builder-v2 342,488 tok -> bar 43% (=> ~796,484). Both land at
# ~800k, i.e. ~200k reserved. So a jsonl-token fallback must divide by this
# EFFECTIVE ceiling to agree with the status bar the human (and GM) reads.
_AUTOCOMPACT_RESERVE_1M = 200_000
_RESERVE_FRACTION = 0.20   # applied proportionally to non-[1m] ceilings


def effective_ceiling(model: str) -> Optional[int]:
    """The auto-compact-aware ceiling the TUI bar is measured against.

    [1m]: 1,000,000 - 200,000 reserved = 800,000 (calibrated).
    standard: 200,000 * (1 - 0.20) = 160,000 (proportional).

    Returns None for an unknown model — see ceiling(). Callers must not divide
    by it without checking.

    FLAGGED, NOT FIXED (2026-09-29): the subtraction below is an AUTO-COMPACT
    reserve — headroom Claude Code held back for the response when auto-compact
    was on. `autoCompactEnabled` is currently false fleet-wide, so this reserve
    is probably no longer real and these ceilings may be ~20% too low. That is
    the SAME coupling that hid the original bug (a detector tied to an
    auto-compact feature that was later switched off), so it is called out
    rather than silently adjusted: re-calibrating it needs a live measurement
    against the TUI, which is not possible while no pane renders a context bar.
    Deliberately left alone — being 20% conservative fails safe, whereas
    guessing a new reserve does not.
    """
    raw = ceiling(model)
    if raw is None:
        return None
    if _is_codex_window_model((model or "").lower()):
        return raw          # codex: used_pct divides by the FULL window
    if raw >= 1_000_000:
        return raw - _AUTOCOMPACT_RESERVE_1M
    return int(raw * (1 - _RESERVE_FRACTION))


def context_pct(observed: dict) -> Optional[float]:
    """Compute context fill fraction (0.0..1.0) from observations.

    Prefers the status-bar % (already ceiling-aware). Falls back to
    jsonl_tokens / ceiling(model). Returns None when neither is present.
    """
    status_bar_pct = observed.get("status_bar_pct")
    if status_bar_pct is not None:
        return status_bar_pct / 100.0

    jsonl_tokens = observed.get("jsonl_tokens")
    if jsonl_tokens is not None:
        c = ceiling(observed.get("model", ""))
        if c:                       # unknown model -> no opinion, never a guess
            return jsonl_tokens / c

    return None


def tier(pct: Optional[float]) -> str:
    """Classify a context fraction into a lineage-daemon tier.

    None      -> "unknown"
    < 0.70    -> "ok"
    [0.70,0.80)-> "SOFT" (quality tier: finish atomic step, then hand off)
    >= 0.80   -> "HARD"  (survival tier: act now)
    """
    if pct is None:
        return "unknown"
    if pct < 0.70:
        return "ok"
    if pct < 0.80:
        return "SOFT"
    return "HARD"
