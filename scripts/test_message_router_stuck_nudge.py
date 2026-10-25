"""A machine-authored nudge line stuck in an UNATTACHED pane's composer ("Check your inbox: ...",
typed, non-dim) holds every delivery to that seat (not-idle every cycle) because the self-cleanup
only knows its own '[MSG from' chips. The router recognises the known nudge shapes, submits them
with a separate bare Enter, and logs it. Human text and ghost suggestions are never touched.
Fake tmux only."""
import importlib.util
import inspect
import os
from types import SimpleNamespace

import pytest

_here = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location("message_router_nudge", os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)

NUDGE = "Check your inbox: python3 ~/orchestra/msg_store.py inbox --agent alpha"
STUCK = "\n".join(["\u25cf Some earlier work", "  \u23bf result", "\u276f " + NUDGE,
                   "  \u23f5\u23f5 bypass permissions on"])
STUCK_ANSI = STUCK.replace("\u276f Check", "\u276f \x1b[0mCheck")      # default colour = typed
GHOST_ANSI = STUCK.replace("\u276f Check", "\u276f \x1b[2mCheck")      # dim = ghost suggestion
HUMAN = STUCK.replace(NUDGE, "please look at the crash in the build and tell me")
HUMAN_ANSI = HUMAN.replace("\u276f please", "\u276f \x1b[0mplease")
EMPTY = "\n".join(["\u25cf done", "\u276f ", "  \u23f5\u23f5 bypass permissions on"])


class FakeTmux:
    def __init__(self, plain, ansi):
        self.plain, self.ansi, self.keys = plain, ansi, []

    def __call__(self, *args):
        if args[0] == "send-keys":
            self.keys.append(args[-1])
            if args[-1] == "Enter":
                self.plain, self.ansi = EMPTY, EMPTY      # a submitted line leaves the composer
            return SimpleNamespace(returncode=0, stdout="")
        return SimpleNamespace(returncode=0, stdout=(self.ansi if "-e" in args else self.plain))


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(mr.time, "sleep", lambda *_: None)
    monkeypatch.setattr(mr, "log", lambda *a, **k: None)


def _use(monkeypatch, plain, ansi):
    t = FakeTmux(plain, ansi)
    monkeypatch.setattr(mr, "tmux", t)
    return t


def test_detects_stuck_bot_nudge_typed_not_ghost(monkeypatch):
    _use(monkeypatch, STUCK, STUCK_ANSI)
    assert (mr.stuck_bot_nudge("s") or "").startswith("Check your inbox")
    _use(monkeypatch, STUCK, GHOST_ANSI)
    assert mr.stuck_bot_nudge("s") is None                     # ghost suggestion: not stuck


def test_human_text_is_never_a_bot_nudge(monkeypatch):
    _use(monkeypatch, HUMAN, HUMAN_ANSI)
    assert mr.stuck_bot_nudge("s") is None


def test_submit_presses_one_bare_enter_and_verifies(monkeypatch):
    t = _use(monkeypatch, STUCK, STUCK_ANSI)
    assert mr.submit_stuck_bot_nudge("s") is True
    assert t.keys == ["Enter"]                                 # one bare Enter, no text re-sent
    assert mr.stuck_bot_nudge("s") is None


def test_submit_is_noop_on_human_text_and_empty_composer(monkeypatch):
    t = _use(monkeypatch, HUMAN, HUMAN_ANSI)
    assert mr.submit_stuck_bot_nudge("s") is False and t.keys == []
    t = _use(monkeypatch, EMPTY, EMPTY)
    assert mr.submit_stuck_bot_nudge("s") is False and t.keys == []


@pytest.mark.parametrize("runtime", ["gemini", "grok", "mystery"])
def test_multi_line_or_unverified_composers_are_never_touched(monkeypatch, runtime):
    t = _use(monkeypatch, STUCK, STUCK_ANSI)
    assert mr.stuck_bot_nudge("s", runtime) is None
    assert mr.submit_stuck_bot_nudge("s", runtime) is False and t.keys == []


def test_known_nudge_shapes():
    for line in ("Check your inbox: python3 x", "check inbox for new messages",
                 "Drain your inbox now: python3 msg_store.py inbox --agent gm",
                 "You are promoted to canonical. Drain your inbox now",
                 "[MSG from gm | high] see /tmp/agent-msg-x.md",
                 "Read /tmp/agent-msg-abc.md and act on it",
                 "Read your durable inbox with: python3 ~/orchestra/msg_store.py inbox --agent alpha. "
                 "Continue the assigned plan."):
        assert mr.is_bot_nudge_text(line), line
    for line in ("please check the build", "what files are in this folder?", "rm -rf build && make", ""):
        assert not mr.is_bot_nudge_text(line), line


def test_cron_pass_runs_it_after_self_cleanup_on_unattached_panes():
    src = inspect.getsource(mr.main)
    i = src.index("cleanup_stuck_injection(sess, store, _crt)")
    j = src.index("submit_stuck_bot_nudge(sess, _crt)")
    assert i < j and src.index("if attached > 0:") < i
