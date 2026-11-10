"""resume_command_for restores a claude model variant and never emits sentinels."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import promote_successor as ps  # noqa: E402


def test_claude_resume_includes_quoted_model_variant():
    cmd = ps.resume_command_for("claude", "sess-1", "claude-opus-5[1m]")
    assert cmd.startswith("claude --resume sess-1 --dangerously-skip-permissions")
    assert "--model" in cmd
    assert "claude-opus-5[1m]" in cmd


def test_claude_resume_omits_sentinel_models():
    for s in ("unknown", "n/a", "none", "null", "-", "UNKNOWN"):
        cmd = ps.resume_command_for("claude", "sess-1", s)
        assert "--model" not in cmd, s
        assert cmd == "claude --resume sess-1 --dangerously-skip-permissions"


def test_claude_resume_without_model_unchanged():
    assert ps.resume_command_for("claude", "sess-1") == (
        "claude --resume sess-1 --dangerously-skip-permissions")
    assert ps.resume_command_for("claude", "sess-1", "") == (
        "claude --resume sess-1 --dangerously-skip-permissions")
