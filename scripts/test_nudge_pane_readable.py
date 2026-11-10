"""pane_readable, RC_NOT_SENT for an unreadable target, and --dry-run."""
from __future__ import annotations

import types
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nudge_pane as NP  # noqa: E402


class _R:
    def __init__(self, rc, stdout="", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


def test_pane_readable_follows_capture_rc(monkeypatch):
    monkeypatch.setattr(NP, "tmux", lambda *a: _R(1 if "capture-pane" in a else 0))
    assert NP.pane_readable("missing") is False
    monkeypatch.setattr(NP, "tmux", lambda *a: _R(0, "❯ \n"))
    assert NP.pane_readable("gm") is True


def test_unreadable_target_is_not_sent_not_refused_or_submitted(monkeypatch):
    keys = []
    def tmux(*a):
        keys.append(a)
        if a[0] == "capture-pane":
            return _R(1, stderr="can't find pane")
        return _R(0)
    monkeypatch.setattr(NP, "tmux", tmux)
    monkeypatch.setattr(NP, "attached", lambda s: False)
    assert NP._nudge_unlocked("no-such", "hello") == NP.RC_NOT_SENT
    assert not any(a[0] == "send-keys" for a in keys)


def test_dry_run_does_not_type(monkeypatch):
    keys = []
    def tmux(*a):
        keys.append(a)
        if a[0] == "capture-pane":
            return _R(0, "❯ \n")
        return _R(0)
    monkeypatch.setattr(NP, "tmux", tmux)
    monkeypatch.setattr(NP, "attached", lambda s: False)
    assert NP.nudge("gm", "hello", dry_run=True) == 0
    assert not any(a[0] == "send-keys" for a in keys)


def test_dry_run_still_reports_unreadable(monkeypatch):
    monkeypatch.setattr(NP, "tmux", lambda *a: _R(1))
    assert NP.nudge("gone", "hello", dry_run=True) == NP.RC_NOT_SENT


def test_submit_loop_unreadable_is_not_sent(monkeypatch):
    calls = {"n": 0}
    def tmux(*a):
        if a[0] == "capture-pane":
            calls["n"] += 1
            # readable for the pre-check + first composer read; fail after Enter
            if calls["n"] >= 4:
                return _R(1)
            return _R(0, "❯ hello\n" if calls["n"] in (2, 3) else "❯ \n")
        return _R(0)
    monkeypatch.setattr(NP, "tmux", tmux)
    monkeypatch.setattr(NP, "attached", lambda s: False)
    monkeypatch.setattr(NP, "composer_line", lambda s: "hello" if calls["n"] < 4 else "")
    # drive the unlocked path with a stub journal
    import types as T
    journal = T.SimpleNamespace(
        record=lambda *a: None, mark_typed=lambda *a: None,
        clear=lambda *a: None, is_ours=lambda line, text: True,
    )
    sys.modules.setdefault("inject_journal", journal)
    monkeypatch.setitem(sys.modules, "inject_journal", journal)
    # After type+enter, if pane becomes unreadable, report 3 not 0
    # Use real _nudge_unlocked with pane_readable flipping after writes.
    reads = {"c": 0}
    def readable(session):
        reads["c"] += 1
        return reads["c"] < 3  # first checks pass; after Enter, fail
    monkeypatch.setattr(NP, "pane_readable", readable)
    monkeypatch.setattr(NP, "composer_line", lambda s: "" if reads["c"] == 1 else "hello")
    # before: empty composer (idle). after enter: empty would look like submit — readable fails first
    def cl(s):
        # first composer_line (before type): empty
        if reads["c"] <= 1:
            return ""
        return "hello"
    monkeypatch.setattr(NP, "composer_line", cl)
    rc = NP._nudge_unlocked("gm", "hello", settle=0)
    assert rc == NP.RC_NOT_SENT
