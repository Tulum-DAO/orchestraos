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
        return {"state": "working", "activity": "x", "process": {"runtime": "claude"}}


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


# ─────────────────────────────── codex R1 on #471
class _DetState:
    def __init__(self, state, menu=None):
        self.r = {"state": state, "activity": "x", "process": {"runtime": "claude"}}
        if menu:
            self.r["pending_menu"] = menu

    def get_agent_status(self, sess):
        return self.r


def _env(monkeypatch, state, screens):
    calls, seq = [], list(screens)

    def tmux(*a, timeout=5):
        calls.append(a)
        out = (seq.pop(0) if len(seq) > 1 else seq[0]) if a[0] == "capture-pane" else ""
        return types.SimpleNamespace(returncode=0, stdout=out)
    monkeypatch.setattr(G, "_tmux", tmux)
    monkeypatch.setattr(G, "_agent_status", lambda: _DetState(state))
    monkeypatch.setattr(G, "INJECT_INGEST_WAIT_S", 0.0)
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    return calls


def _typed(calls):
    return [c for c in calls if c[0] == "send-keys" and "-l" in c]


def test_a_fresh_menu_is_refused_even_with_force_and_no_pending_menu_flag(monkeypatch):
    """The detector read idle a moment before the menu came up: no pending_menu. A selected
    menu row ("❯ 1. Yes") is not a composer, so nothing is typed, force or not."""
    menu = "Do you want to proceed?\n❯ 1. Yes\n  2. No\n"
    for force in (False, True):
        calls = _env(monkeypatch, "idle", [menu])
        ok, info = G.verified_inject("tgt", "hello", force)
        assert not ok and info["reason"] == "busy", (force, info)
        assert not _typed(calls)


def test_a_draft_whose_first_row_is_empty_is_still_the_operators():
    screen = f"{SPIN}\n❯ \n  second row of a draft the operator is typing\n──────\n"
    assert G._typed_on_prompt_line(screen) == "second row of a draft the operator is typing"


def test_a_swallowed_Enter_on_a_WRAPPED_message_is_not_reported_delivered(monkeypatch):
    """Our 40-char marker wraps across two rows of the box: a marker read row by row is never
    found, and a swallowed Enter would read as delivered."""
    text = "please re-run the whole migration suite and report back"
    pre = "out\n❯ \n──────\n"
    stuck = "out\n❯ please re-run the whole migr\n  ation suite and report back\n──────\n"
    calls = _env(monkeypatch, "idle", [pre, stuck])
    ok, info = G.verified_inject("tgt", text)
    assert not ok and info["reason"] == "unverified_submit", info


def test_the_model_switcher_never_types_into_a_working_agent(monkeypatch):
    """/model typed mid-turn is QUEUED and runs after the switcher restored the host setting: the
    CLI would rewrite the host default unwatched. idle_only refuses it, by state and by screen."""
    calls = _env(monkeypatch, "working", [f"{SPIN}\n❯ \n"])
    ok, info = G.verified_inject("tgt", "/model claude-sonnet-5-5", False, idle_only=True)
    assert not ok and info["reason"] == "busy" and not _typed(calls)
    calls = _env(monkeypatch, "working", ["  ⎿ a subagent is running\n❯ \n"])   # by state only, no spinner
    ok, info = G.verified_inject("tgt", "/model claude-sonnet-5-5", False, idle_only=True)
    assert not ok and info["reason"] == "busy" and not _typed(calls)
    calls = _env(monkeypatch, "idle", [f"{SPIN}\n❯ \n"])          # detector lags: screen shows a turn
    ok, info = G.verified_inject("tgt", "/model claude-sonnet-5-5", False, idle_only=True)
    assert not ok and info["reason"] == "busy" and not _typed(calls)


def test_the_agent_model_handler_sends_idle_only():
    src = (Path(__file__).resolve().parent / "watch_gateway.py").read_text()
    assert "send=lambda text: verified_inject(session, text, False, idle_only=True)" in src


# ─────────────────────────────── codex R2 on #471
def test_a_fresh_menu_under_an_old_prompt_line_in_scrollback_is_still_refused(monkeypatch):
    menu = "❯ an earlier request, already sent\n  ⎿ reply\nDo you want to proceed?\n❯ 1. Yes\n  2. No\n"
    assert not G._pane_has_composer(menu)
    for force in (False, True):
        calls = _env(monkeypatch, "idle", [menu])
        ok, info = G.verified_inject("tgt", "hello", force)
        assert not ok and info["reason"] == "busy", (force, info)
        assert not _typed(calls)


def test_a_normal_composer_under_old_prompt_lines_still_accepts():
    assert G._pane_has_composer("❯ 1. an old numbered message\n  ⎿ ok\n──────\n❯ \n──────\n")


def test_quoted_esc_to_interrupt_in_old_output_does_not_block_an_idle_model_switch(monkeypatch):
    screen = ("  the docs say: press esc to interrupt a turn\n  ⎿ done\n" + "x\n" * 8
              + "──────\n❯ \n──────\n")
    calls = _env(monkeypatch, "idle", [screen, "──────\n❯ \n──────\n"])
    ok, info = G.verified_inject("tgt", "/model claude-sonnet-5-5", False, idle_only=True)
    assert ok, info
    assert _typed(calls)


def test_the_menu_row_rule_covers_every_selected_row_the_detectors_menu_parser_reads():
    """_MENU_ROW_RE and the detector's menu parser must agree: a selected row the parser reads as
    a menu option is never a composer here. (A shape the parser does not read is invisible to
    pending_menu too; widening one without the other is the drift this pins.)"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "agent_status_pin", Path(__file__).resolve().parent / "agent-status.py")
    A = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(A)
    rows = ["❯ 1. Yes", "> 2. No, tell Claude what to do differently", "  ❯ 12. Something",
            "› 1. gpt-5.6-terra", "› 3. low"]
    for r in rows:
        assert A._MENU_OPT_RE.match(r) or A._CODEX_SELECTOR_RE.match(r), r     # the parser reads it
        assert G._MENU_ROW_RE.match(r), r                                       # so it is not a composer
    for r in ["❯ hello", "❯ ", "> fix the 1. thing", "› write the tests"]:
        assert not G._MENU_ROW_RE.match(r), r


def test_quoted_esc_to_interrupt_RIGHT_above_the_prompt_does_not_block_an_idle_switch(monkeypatch):
    for quote in ("docs say: press esc to interrupt", "  ⎿ press esc to cancel to stop it"):
        screen = f"output\n{quote}\n──────\n❯ \n──────\n"
        assert not G._turn_above_composer(screen), quote
        calls = _env(monkeypatch, "idle", [screen, "──────\n❯ \n──────\n"])
        ok, info = G.verified_inject("tgt", "/model claude-sonnet-5-5", False, idle_only=True)
        assert ok, (quote, info)


def test_a_real_spinner_right_above_the_prompt_is_a_turn():
    assert G._turn_above_composer(f"{SPIN}\n──────\n❯ \n──────\n")
    assert G._turn_above_composer("◦ Working (12s • esc to interrupt)\n\n› \n")


# ─────────────────────────────── codex R6 on #471
def test_mid_turn_is_accepted_only_for_runtimes_proven_to_queue(monkeypatch):
    screen = f"{SPIN}\n❯ \n"
    for rt, accepted in (("claude", True), ("codex", True), ("gemini", False), ("grok", False), ("", False)):
        calls = _env(monkeypatch, "working", [screen, f"{SPIN}\n❯ \n"])
        monkeypatch.setattr(G, "_agent_status", lambda rt=rt: types.SimpleNamespace(
            get_agent_status=lambda s: {"state": "working", "activity": "x", "process": {"runtime": rt}}))
        monkeypatch.setattr(G, "_composer_signature_verified", lambda r: True)
        ok, info = G.verified_inject("tgt", "hello")
        if accepted:
            assert info.get("reason") != "busy", (rt, info)
        else:
            assert not ok and info["reason"] == "busy" and not _typed(calls), (rt, info)


# ─────────────────────────────── codex R7 on #471 + a live capture
def test_an_INDENTED_glyph_line_above_the_prompt_is_quoted_output_not_a_turn():
    assert not G._turn_above_composer("  ✻ quoted: press esc to interrupt\n──────\n❯ \n──────\n")


def test_the_real_claude_spinner_without_an_esc_hint_is_a_turn():
    """Captured from a live Claude pane mid-turn (2026-10-22): the spinner line has no esc hint."""
    line = "* Fermenting… (1h 14m 31s · ↓ 78.0k tokens · thought for 3s)"
    assert G._turn_above_composer(f"output\n{line}\n\n──────\n❯ \n──────\n")
