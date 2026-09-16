"""Tests for effect-existence checks (RED-TEAM Finding 0.5-B + H6).

'Began the correct work' = a checkable first EFFECT exists, not 'is typing about the
focus'. Effects are the unfoolable rung-4 substrate (exit codes / file hashes /
msg rows / live sessions) — they don't lag once created and can't be ghosted. For
broad-focus agents (gm/ob-class) this task-level anchor IS the check.
"""
from scripts.focus_registry.effects import check_effect


def test_file_effect_exists(tmp_path):
    p = tmp_path / "made.txt"
    p.write_text("x")
    r = check_effect({"kind": "file", "target": "made.txt"}, cwd=str(tmp_path))
    assert r["exists"] is True


def test_file_effect_missing(tmp_path):
    r = check_effect({"kind": "file", "target": "nope.txt"}, cwd=str(tmp_path))
    assert r["exists"] is False


def test_session_effect_via_runner():
    # tmux has-session rc=0 -> exists (injected runner, no real tmux).
    ok = lambda argv, cwd=None: 0
    bad = lambda argv, cwd=None: 1
    assert check_effect({"kind": "session", "target": "canary"}, runner=ok)["exists"] is True
    assert check_effect({"kind": "session", "target": "canary"}, runner=bad)["exists"] is False


def test_commit_effect_via_runner():
    assert check_effect({"kind": "commit", "target": "abc123"}, runner=lambda a, cwd=None: 0)["exists"] is True
    assert check_effect({"kind": "commit", "target": "abc123"}, runner=lambda a, cwd=None: 1)["exists"] is False


def test_command_effect_uses_check_rc():
    eff = {"kind": "command", "target": "n/a", "check": "test -f something"}
    assert check_effect(eff, runner=lambda a, cwd=None: 0)["exists"] is True
    assert check_effect(eff, runner=lambda a, cwd=None: 1)["exists"] is False


def test_unknown_kind_is_false_not_crash():
    r = check_effect({"kind": "telepathy", "target": "x"})
    assert r["exists"] is False


def test_runner_exception_is_fail_open_false():
    def boom(argv, cwd=None):
        raise RuntimeError("no tmux")
    r = check_effect({"kind": "session", "target": "x"}, runner=boom)
    assert r["exists"] is False   # never raises out of the check
