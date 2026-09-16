"""RED — gpt-6-astra codex ceiling entry (gm task msg_2cdb07fa, non-urgent).

Symptom: gpt-6-astra-agent logged 'ctx:out-of-range source=jsonl value=128'
every cron tick — ceiling() fell to the 200k default while the seat's REAL
codex window is 258,400 tokens. Window derived from the AUTHORITATIVE codex
session metadata, never guessed: codex_context.get_codex_session_context
('gpt-6-astra-agent') on 2026-09-15 returned (61.35%, 158,541 used, 258,400
model_context_window).

Codex accounting has NO auto-compact reserve — its own used_pct divides by the
FULL window (codex_context.context_from_info) — so effective_ceiling must
return the raw window for this model, not the 20% proportional reserve.
"""
from lineage_daemon.ctxstate import ceiling, effective_ceiling


def test_ceiling_gpt6_astra_uses_codex_window():
    assert ceiling("gpt-6-astra") == 258_400


def test_effective_ceiling_gpt6_astra_is_raw_window():
    # codex measures against the full window: effective == raw, no reserve.
    assert effective_ceiling("gpt-6-astra") == 258_400


def test_gpt6_astra_pct_matches_codex_truth():
    # The live calibration point: 158,541 used -> codex says 61%.
    assert round(158_541 / effective_ceiling("gpt-6-astra") * 100) == 61


def test_other_models_unchanged():
    assert ceiling("claude-opus-4-8[1m]") == 1_000_000
    assert effective_ceiling("claude-opus-4-8[1m]") == 800_000
    assert ceiling("claude-opus-4") == 200_000
    assert effective_ceiling("claude-opus-4") == 160_000
