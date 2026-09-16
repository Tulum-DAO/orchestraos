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


def ceiling(model: str) -> int:
    """Return the RAW context-window ceiling (in tokens) for a model string."""
    m = (model or "").lower()
    if _is_codex_window_model(m):
        return _GPT6_ASTRA_WINDOW
    if "[1m]" in m or "gemini" in m or "flash" in m or "pro" in m or "agy" in m or "antigravity" in m:
        return 1_000_000
    return 200_000


# The TUI context bar (and auto-compact) does NOT count against the raw ceiling
# -- Claude Code reserves headroom for the response/system, so the bar reads
# higher than tokens/raw_ceiling. Calibrated against two live [1m] sessions on
# 2026-08-12: orchestra-builder 690,334 tok -> bar 86% (=> ~802,714) and
# orchestra-builder-v2 342,488 tok -> bar 43% (=> ~796,484). Both land at
# ~800k, i.e. ~200k reserved. So a jsonl-token fallback must divide by this
# EFFECTIVE ceiling to agree with the status bar the human (and GM) reads.
_AUTOCOMPACT_RESERVE_1M = 200_000
_RESERVE_FRACTION = 0.20   # applied proportionally to non-[1m] ceilings


def effective_ceiling(model: str) -> int:
    """The auto-compact-aware ceiling the TUI bar is measured against.

    [1m]: 1,000,000 - 200,000 reserved = 800,000 (calibrated).
    standard: 200,000 * (1 - 0.20) = 160,000 (proportional; no live standard
    calibration point, but every long-lived fleet session is [1m]).
    """
    raw = ceiling(model)
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
        return jsonl_tokens / ceiling(observed.get("model", ""))

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
