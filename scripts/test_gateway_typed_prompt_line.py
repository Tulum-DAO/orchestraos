"""The send gate's typed-text read, now that a mid-turn pane is typed into.

composer_state's whole-screen classifier reads any screen with a working marker as 'submitted' and
reports no typed text. That was harmless while the gateway refused every mid-turn pane; once a
working pane accepts a message, it would let a send paste over a draft the operator is typing
mid-turn. _typed_on_prompt_line asks the same reader about the live prompt line itself.
Real ANSI (SGR) captures: dim = ghost suggestion, default style = typed.
"""
import importlib
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
G = importlib.import_module("watch_gateway")

DIM, RESET, REV = "\x1b[2m", "\x1b[0m", "\x1b[7m"
SPIN = "\x1b[38;5;174m✻\x1b[39m Sautéing… (esc to interrupt)"


def test_a_draft_typed_mid_turn_is_read_as_typed():
    screen = f"{SPIN}\n❯ half a sentence the operator is writing{REV} {RESET}\n"
    assert G._typed_on_prompt_line(screen) == "half a sentence the operator is writing"


def test_a_dim_ghost_mid_turn_is_not_typed():
    screen = f"{SPIN}\n❯ {REV}{DIM}T{RESET}{DIM}ry \"fix the tests\"{RESET}\n"
    assert G._typed_on_prompt_line(screen) == ""


def test_an_empty_prompt_mid_turn_is_empty_and_no_prompt_is_none():
    assert G._typed_on_prompt_line(f"{SPIN}\n❯ {REV} {RESET}\n") == ""
    assert G._typed_on_prompt_line(f"{SPIN}\n  ⎿ output only\n") is None


def test_only_the_BOTTOM_prompt_line_counts_not_submitted_ones_in_scrollback():
    screen = f"❯ an old message already sent\n  ⎿ reply\n{SPIN}\n❯ {REV} {RESET}\n"
    assert G._typed_on_prompt_line(screen) == ""


def test_the_whole_screen_classifier_would_have_missed_the_draft():
    """The reason for the line read: the compat reader says '' on this very screen."""
    from scripts.composer_state import composer_text
    screen = f"{SPIN}\n❯ half a sentence the operator is writing{REV} {RESET}\n"
    assert composer_text(screen) == ""                       # blind mid-turn
    assert G._typed_on_prompt_line(screen)                   # not blind


class _Det:
    def get_agent_status(self, sess):
        return {"state": "working", "activity": "x", "process": {}}


def test_verified_inject_refuses_over_an_ANSI_draft_mid_turn(monkeypatch):
    screen = f"{SPIN}\n❯ half a sentence the operator is writing{REV} {RESET}\n"
    calls = []

    def tmux(*a, timeout=5):
        calls.append(a)
        return types.SimpleNamespace(returncode=0, stdout=screen if a[0] == "capture-pane" else "")
    monkeypatch.setattr(G, "_tmux", tmux)
    monkeypatch.setattr(G, "_agent_status", lambda: _Det())
    ok, info = G.verified_inject("tgt", "hello")
    assert not ok and info["reason"] == "busy" and info["composer_text"]
    assert not [c for c in calls if c[0] == "send-keys"]
