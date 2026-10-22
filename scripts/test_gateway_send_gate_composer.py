"""RED-first: the send gate turns on COMPOSER-ACCEPTS-INPUT, not state=working.

The operator's P1 (gm ruling msg_627b54e3, with orchestraos-builder msg_57b0fe6f): chat could not
send to a seat that was "working" with a subagent running, even though its CLI composer
accepts and QUEUES input. Claude Code queues mid-turn natively, so refusing a mid-turn pane
with a live composer invented a problem the CLI does not have.

THE NEW PREDICATE — all three, and nothing about state=working:
  1. a prompt box is PRESENT on the visible screen (the composer exists), AND
  2. no pending menu / permission prompt (those REPLACE the composer and need the operator), AND
  3. the pane is ALIVE (a dead/stopped/crashed pane has no composer to queue into).

WHAT MUST STILL BE REFUSED, unchanged: a menu/permission prompt, a dead or stopped pane,
and the operator's own unsubmitted typed text (the standing sacred-composer rule).

⚠ THE TRAP THIS CHANGE CREATES, and the reason the submit verification moves too:
verified_inject accepted a submit when `turn_started or not still_in_composer`. Mid-turn a
turn is ALREADY running, so `turn_started` is true BEFORE we type anything — it would read a
swallowed Enter as a successful send, which is the exact merge-3 failure class the verified
submit exists to catch. Mid-turn, the only honest witness is that the composer CLEARED.
"""
import importlib
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
G = importlib.import_module("watch_gateway")

WORKING_PANE = ("✻ Sautéing… (esc to interrupt)\n"
                "  ⎿ running a subagent\n"
                "❯ \n")
MENU_PANE = ("Claude needs your permission to run Bash\n"
             " 1. Yes\n 2. Yes, and don't ask again\n 3. No\n")
IDLE_PANE = "some output\n❯ \n"


class _Det:
    def __init__(self, result): self._r = result
    def get_agent_status(self, sess): return self._r


class _Tmux:
    def __init__(self, captures):
        self.calls = []
        self._c = list(captures)
    def __call__(self, *args, timeout=5):
        self.calls.append(args)
        out = self._c.pop(0) if (args[0] == "capture-pane" and self._c) else ""
        return types.SimpleNamespace(returncode=0, stdout=out)


def _env(monkeypatch, state, captures, activity="Running a subagent"):
    t = _Tmux(captures)
    monkeypatch.setattr(G, "_tmux", t)
    monkeypatch.setattr(G, "INJECT_INGEST_WAIT_S", 0.0)
    monkeypatch.setattr(G, "_agent_status",
                        lambda: _Det({"state": state, "activity": activity, "process": {"runtime": "claude"}}))
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    return t


def _sent_text(tmux):
    return [c for c in tmux.calls if c[0] == "send-keys" and "-l" in c]


# ─────────────────────────────── the change itself
def test_working_with_an_accepting_composer_is_INJECTED(monkeypatch):
    """THE BUG: this was refused as busy. The CLI queues it mid-turn."""
    # pre-capture (working + composer), then post-Enter capture with the composer CLEARED
    t = _env(monkeypatch, "working", [WORKING_PANE, "✻ Sautéing… (esc to interrupt)\n❯ \n"])
    ok, info = G.verified_inject("tgt", "run the audit")
    assert ok, f"a mid-turn pane with a live composer must be injected, got {info}"
    assert _sent_text(t), "nothing was actually typed into the pane"


def test_thinking_with_an_accepting_composer_is_INJECTED(monkeypatch):
    t = _env(monkeypatch, "thinking", [WORKING_PANE, "✻ Thinking… (esc to interrupt)\n❯ \n"])
    ok, info = G.verified_inject("tgt", "one more thing")
    assert ok, info


# ─────────────────────────────── the controls that must NOT regress
def test_menu_or_permission_prompt_is_STILL_REFUSED(monkeypatch):
    """A menu REPLACES the composer and needs the operator — never queue on top of it."""
    t = _env(monkeypatch, "waiting_permission", [MENU_PANE])
    ok, info = G.verified_inject("tgt", "hello")
    assert not ok and info["reason"] == "busy"
    assert info["state"] == "waiting", "the API keys its menu branch on state=='waiting'"
    assert not _sent_text(t), "a permission prompt was typed into"


def test_a_dead_or_stopped_pane_is_STILL_REFUSED(monkeypatch):
    t = _env(monkeypatch, "stopped", [""])
    ok, info = G.verified_inject("tgt", "hello")
    assert not ok and info["reason"] == "busy"
    assert not _sent_text(t)


def test_working_with_NO_composer_is_REFUSED(monkeypatch):
    """Mid-turn is fine; mid-turn with no prompt box is not — there is nothing to queue into."""
    t = _env(monkeypatch, "working", ["✻ Sautéing… (esc to interrupt)\n  ⎿ no composer here\n"])
    ok, info = G.verified_inject("tgt", "hello")
    assert not ok and info["reason"] == "busy"
    assert not _sent_text(t)


def test_the_operators_unsubmitted_typed_text_is_STILL_SACRED_even_mid_turn(monkeypatch):
    t = _env(monkeypatch, "working",
             ["✻ Sautéing… (esc to interrupt)\n❯ clear it, and GM draft\n"])
    ok, info = G.verified_inject("tgt", "hello")
    assert not ok and info["reason"] == "busy"
    assert "unsubmitted" in info["activity"].lower()
    assert info.get("composer_text")
    assert not _sent_text(t)


# ─────────────────────────────── the verification trap
def test_mid_turn_submit_is_NOT_proven_by_a_turn_that_was_already_running(monkeypatch):
    """A swallowed Enter mid-turn must read as unverified_submit, never as sent.

    Every post-Enter capture still shows OUR text in the composer while the pre-existing
    spinner keeps `turn_started` true. Keying success on turn_started would call this a
    successful send — the merge-3 class the verified submit exists to catch.
    """
    # PRE capture must show an EMPTY composer — otherwise the sacred-typed-text guard
    # refuses first and this never reaches the verification it is meant to test. (My first
    # fixture made that mistake and "passed" for the wrong reason.)
    stuck = "✻ Sautéing… (esc to interrupt)\n❯ run the audit\n"
    t = _env(monkeypatch, "working", [WORKING_PANE, stuck, stuck, stuck, stuck])
    ok, info = G.verified_inject("tgt", "run the audit")
    assert not ok, "a mid-turn swallowed Enter must not report success"
    assert info["reason"] == "unverified_submit", info


def test_an_idle_pane_still_counts_a_turn_STARTING_as_proof(monkeypatch):
    """The legacy witness is still valid when no turn was running beforehand."""
    t = _env(monkeypatch, "idle", [IDLE_PANE, "✻ Working… (esc to interrupt)\n❯ hello\n"])
    ok, info = G.verified_inject("tgt", "hello")
    assert ok, f"a turn that STARTED is proof when none was running before: {info}"


def test_a_pending_menu_is_refused_even_when_it_LOOKS_like_a_composer(monkeypatch):
    """The sabotage that did NOT fire the first time, and the hazard it exposed.

    Letting waiting_permission into the allowed set changed NOTHING, because the menu pane in
    the control above happens to have no prompt line. But `_is_prompt_line` treats a leading
    '>' as a prompt, and a real permission menu renders its selected option as "> 1. Yes" —
    verified against the live function. So the composer check alone would have typed into a
    permission prompt, and gm's control would have rested on an accident of rendering.
    The detector's own `pending_menu` is now an independent refusal.
    """
    menu_that_looks_like_a_composer = (
        "Claude needs your permission to run Bash\n"
        " > 1. Yes\n"
        "   2. No, tell Claude what to do differently\n")
    assert G._is_prompt_line(" > 1. Yes"), (
        "fixture must reproduce the hazard: the selected option reads as a prompt line")
    # Since codex R1 on #471 the composer gate also ignores a selected menu row, so the menu is
    # refused by BOTH mechanisms; this test still pins the pending_menu half.
    assert not G._pane_has_composer(menu_that_looks_like_a_composer)
    t = _Tmux([menu_that_looks_like_a_composer])
    monkeypatch.setattr(G, "_tmux", t)
    monkeypatch.setattr(G, "INJECT_INGEST_WAIT_S", 0.0)
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    # detector state LAGS at 'working' while the menu is already on screen
    monkeypatch.setattr(G, "_agent_status", lambda: _Det(
        {"state": "working", "activity": "x", "process": {"runtime": "claude"},
         "pending_menu": {"question": "run Bash?", "options": [{"n": "1", "label": "Yes"}]}}))
    ok, info = G.verified_inject("tgt", "hello")
    assert not ok and info["reason"] == "busy"
    assert info["state"] == "waiting", info
    assert not _sent_text(t), "typed into a pending permission menu"
