"""RED-first: the codex refusal lifts ONLY under codex §4's 7-condition gate.

codex-dev-1 §4 (msg_ec87a49f): lift the Codex auto-recovery refusal for a specific
rotation ONLY when ALL are true — (1) real codex SID resolves + transcript confirms,
(2) pred/succ instance records complete+fresh, (3) recovery owns a live lease +
current epoch + CAS state, (4) durable txn identifies exactly one canonical
successor, (5) projections verified/recoverable, (6) successor first-effect verified
BEFORE predecessor retire, (7) checks enforced (not shadow) in canary scope.
"Fence satisfiable alone is insufficient." Absent a fully-satisfied gate, the
refusal STANDS (fail-closed). claude/gemini admission stays byte-unchanged.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from recovery_runtime import (  # noqa: E402
    automatic_resume_runtime, AutomaticRecoveryRefused)


_ALL_SEVEN = {
    "codex_sid_resolved_and_transcript_confirmed": True,
    "instance_records_complete_and_fresh": True,
    "recovery_owns_live_lease_epoch_cas": True,
    "durable_txn_exactly_one_successor": True,
    "projections_verified_or_recoverable": True,
    "successor_first_effect_verified": True,
    "checks_enforced_not_shadow": True,
}


def test_codex_still_refused_with_no_gate():
    # Default (no codex_gate) — refusal unchanged (fence-satisfiable alone is NOT
    # enough; the caller must present a fully-satisfied 7-condition gate).
    with pytest.raises(AutomaticRecoveryRefused):
        automatic_resume_runtime({"runtime": "codex"})


def test_codex_admitted_when_all_seven_conditions_true():
    assert automatic_resume_runtime(
        {"runtime": "codex"}, codex_gate=dict(_ALL_SEVEN)) == "codex"


@pytest.mark.parametrize("missing", list(_ALL_SEVEN))
def test_codex_refused_when_any_single_condition_false(missing):
    gate = dict(_ALL_SEVEN)
    gate[missing] = False
    with pytest.raises(AutomaticRecoveryRefused):
        automatic_resume_runtime({"runtime": "codex"}, codex_gate=gate)


def test_codex_refused_when_a_condition_key_is_absent():
    gate = dict(_ALL_SEVEN)
    del gate["successor_first_effect_verified"]
    with pytest.raises(AutomaticRecoveryRefused):
        automatic_resume_runtime({"runtime": "codex"}, codex_gate=gate)


def test_codex_gate_ignored_for_claude_and_gemini_bytewise():
    # A codex_gate must NOT change the claude/gemini path in any way.
    assert automatic_resume_runtime({"runtime": "claude"}, codex_gate=dict(_ALL_SEVEN)) == "claude"
    assert automatic_resume_runtime({"runtime": "gemini"}, codex_gate=dict(_ALL_SEVEN)) == "gemini"


def test_claude_gemini_unchanged_without_gate():
    assert automatic_resume_runtime({"runtime": "claude"}) == "claude"
    assert automatic_resume_runtime({"runtime": "gemini"}) == "gemini"
