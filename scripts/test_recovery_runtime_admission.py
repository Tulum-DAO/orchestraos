"""Guard tests for recovery_runtime.automatic_resume_runtime.

recovery_runtime had ZERO coverage. These pin the CURRENT admission behavior as
the no-collateral baseline (gm's requirement): when piece-1 narrows the codex
refusal, the claude/gemini admission paths and the unresolved-row refusal MUST
stay byte-identical in behavior. Any drift here is a regression.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from recovery_runtime import (  # noqa: E402
    automatic_resume_runtime, AutomaticRecoveryRefused)
from runtime_signatures import RuntimeResolutionError  # noqa: E402


def test_claude_row_admitted():
    assert automatic_resume_runtime({"runtime": "claude"}) == "claude"


def test_gemini_row_admitted():
    assert automatic_resume_runtime({"runtime": "gemini"}) == "gemini"


def test_agy_normalizes_to_gemini():
    # agy is gemini's declared alias; the strict resolver normalizes it.
    assert automatic_resume_runtime({"runtime": "agy"}) == "gemini"


def test_unknown_runtime_refused():
    # Characterized behavior: a truly-unknown runtime is rejected by the STRICT
    # resolver (RuntimeResolutionError) before the recovery policy layer runs —
    # it never silently defaults to claude. This must stay byte-identical after
    # the codex lift (the lift touches only the codex branch).
    with pytest.raises(RuntimeResolutionError):
        automatic_resume_runtime({"runtime": "totally-made-up"})


def test_codex_currently_refused_without_fence():
    # Baseline: a codex row with NO process-instance fence evidence is refused.
    # (Piece-1 lift will admit codex ONLY when the fence is satisfied; a bare row
    # with no fence must keep refusing.)
    with pytest.raises(AutomaticRecoveryRefused):
        automatic_resume_runtime({"runtime": "codex"})
