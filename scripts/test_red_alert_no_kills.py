#!/usr/bin/env python3
"""HARD RULE (the operator, 2026-09-18): the self-heal loop NEVER kills anything.

red-alert-builder's `kill 169668` killed the tmux server and took the whole fleet down
(seen once). This test fails if red_alert.py / red_alert_watch.py contain a kill-class call
(kill/pkill/killall, SIGTERM/SIGKILL/SIGSTOP, tmux kill-session/kill-server, respawn-pane -k)
or a history-rewriting / tree-discarding git command. Comments and docstrings are stripped
before scanning so the rule can be DOCUMENTED in the scripts without tripping it.
"""
import io
import os
import re
import tokenize

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = ["red_alert.py", "red_alert_watch.py"]

FORBIDDEN = [
    r"\bos\.kill\b", r"\bos\.killpg\b", r"\bsignal\.SIG(?:TERM|KILL|STOP|TSTP|INT)\b",
    r"\bProc(?:ess)?\.kill\b", r"\.terminate\(", r"\.kill\(",
    r"[\"']kill[\"']", r"[\"']pkill[\"']", r"[\"']killall[\"']",
    r"kill-session", r"kill-server", r"kill-pane", r"kill-window",
    r"respawn-pane[\"'],\s*[\"']-k[\"']", r"[\"']-k[\"']",
    r"git\W+(?:reset\s+--hard|checkout\s+--|clean\s+-|stash|push\s+--force|rebase|filter-branch)",
]


def code_only(path: str) -> str:
    """Source with comments and string literals removed (strings hold the docs of the rule)."""
    src = open(path).read()
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT,):
            continue
        if tok.type == tokenize.STRING:
            # keep short strings (argv tokens like "-k", "kill-session") — those ARE the calls we hunt;
            # drop long ones (docstrings / prose)
            if len(tok.string) > 40:
                continue
        out.append(tok.string + " ")
    return "".join(out)


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("pattern", FORBIDDEN)
def test_no_kill_class_call(script, pattern):
    text = code_only(os.path.join(HERE, script))
    m = re.search(pattern, text)
    assert not m, f"{script}: forbidden call /{pattern}/ -> {text[max(0, m.start()-60):m.end()+40]!r}"


def test_respawn_only_when_process_gone(monkeypatch):
    import sys
    sys.path.insert(0, HERE)
    import red_alert_watch as W
    # this test must NEVER reach tmux (its first version respawned the live gm pane — see docs/RED_ALERT.md)
    monkeypatch.setattr(W, "tmux", lambda *a: (_ for _ in ()).throw(AssertionError("tmux must not be called")))
    ev = {"process_state": {"gm": [{"pid": 1, "stat": "T", "cmd": "claude --resume abc"}]},
          "registry_rows": {"gm": {"session_id": "abc"}}, "pane_dead": {"gm": False}}
    ok, detail = W.repair_respawn("gm", "gm", ev)
    assert ok is False and "still present" in detail
