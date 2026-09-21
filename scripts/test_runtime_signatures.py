"""RED-first (issue #92, fresh-install DX report 2026-09-20): a Codex seat was launched with
`--model claude-sonnet-5`; the CLI exited and the init prompt was typed into bare bash. Nothing
checked that a model id belongs to the declared runtime. The check is one shared function,
positive-signal only, that the spawn path refuses on."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime_signatures as rs  # noqa: E402


@pytest.mark.parametrize("model,expected", [
    ("claude-sonnet-5", "claude"), ("claude-opus-4-8[1m]", "claude"), ("Claude-Fable-5-1", "claude"),
    ("gemini-2.5-pro", "gemini"),
    ("gpt-5.6-terra", "codex"), ("o3", "codex"), ("codex-mini-latest", "codex"),
    ("", None), (None, None), ("mystery-model", None),
])
def test_model_runtime_positive_signal_only(model, expected):
    assert rs.model_runtime(model) == expected


def test_validate_refuses_a_model_of_another_runtime():
    with pytest.raises(rs.RuntimeResolutionError) as e:
        rs.validate_model_for_runtime("codex", "claude-sonnet-5", agent_id="codex-helper")
    assert "claude-sonnet-5" in str(e.value) and "codex" in str(e.value)


def test_validate_accepts_matching_and_unknown_models():
    rs.validate_model_for_runtime("codex", "gpt-5.6-terra")
    rs.validate_model_for_runtime("claude", "claude-opus-4-8[1m]")
    rs.validate_model_for_runtime("gemini", "")            # no model = nothing to contradict
    rs.validate_model_for_runtime("claude", "mystery-model")  # unknown family: not a mismatch
