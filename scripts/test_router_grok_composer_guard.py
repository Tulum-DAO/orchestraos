"""Grok composer guard: the stuck-injection pass (which sends C-u / Enter before the idle gate)
must match nothing on a grok pane unless delivery is armed AND the live grok composer, the
prompt line with the grok footer under it, holds typed text. Fake tmux only."""
import importlib.util
import os
import sys
import types
from types import SimpleNamespace

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
import runtime_signatures as rs  # noqa: E402

_spec = importlib.util.spec_from_file_location("message_router_grok_guard",
                                               os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)

# A measured-style signature (the shipped grok signature is an unverified sentinel).
SIG = {"prompt_char": "\u276f", "idle_footer_re": r"Grok \d+(?:\.\d+)* \([^)]*\) · ", "verified": True}
FOOTER = "\x1b[2mGrok 1.2.3 (high) \x1b[38;5;3m·\x1b[0m auto"


def _screen(composer):
    return "\n".join(["● earlier reply", "\u276f an old line in scrollback", "",
                      f"\x1b[0m\u276f{composer}\x1b[0m", "", FOOTER])


def test_classifier_reads_empty_typed_and_absent():
    assert rs.grok_composer_line_state(_screen(""), SIG) == "empty"
    assert rs.grok_composer_line_state(_screen(" [MSG from a | high] hi"), SIG) == "typed"
    # a prompt glyph only in scrollback, no footer under it: not a composer
    assert rs.grok_composer_line_state("● x\n\u276f typed\n● more", SIG) == "absent"


def test_shipped_unverified_signature_is_always_absent():
    assert rs.grok_composer_line_state(_screen(" typed"), None) == "absent"
    assert rs.grok_composer_line_state(_screen(" typed"), {"prompt_char": "\u276f"}) == "absent"


def _fake_grok_context(monkeypatch, armed):
    mod = types.ModuleType("grok_context")
    if isinstance(armed, Exception):
        def _boom():
            raise armed
        mod.delivery_armed = _boom
    else:
        mod.delivery_armed = lambda: armed
    monkeypatch.setitem(sys.modules, "grok_context", mod)


def _tmux(monkeypatch, out, rc=0):
    calls = []

    def fake(*a):
        calls.append(a)
        return SimpleNamespace(returncode=rc, stdout=out)
    monkeypatch.setattr(mr, "tmux", fake)
    return calls


def test_unarmed_grok_matches_nothing_and_never_captures(monkeypatch):
    _fake_grok_context(monkeypatch, False)
    calls = _tmux(monkeypatch, _screen(" [MSG from a | high] hi"))
    assert mr._grok_live_composer_is_typed("p", SIG) is False
    assert calls == []


def test_reader_fault_or_capture_failure_never_matches(monkeypatch):
    _fake_grok_context(monkeypatch, RuntimeError("boom"))
    _tmux(monkeypatch, _screen(" typed"))
    assert mr._grok_live_composer_is_typed("p", SIG) is False
    _fake_grok_context(monkeypatch, True)
    _tmux(monkeypatch, "", rc=1)
    assert mr._grok_live_composer_is_typed("p", SIG) is False


def test_armed_and_typed_live_composer_matches(monkeypatch):
    _fake_grok_context(monkeypatch, True)
    _tmux(monkeypatch, _screen(" [MSG from a | high] hi"))
    assert mr._grok_live_composer_is_typed("p", SIG) is True
    _tmux(monkeypatch, _screen(""))
    assert mr._grok_live_composer_is_typed("p", SIG) is False


def test_stuck_own_message_on_an_unarmed_grok_pane_is_none(monkeypatch):
    _fake_grok_context(monkeypatch, False)
    calls = _tmux(monkeypatch, _screen(" [MSG from a | high] hi"))
    assert mr.stuck_own_message("p", "grok") is None
    assert calls == []


def test_claude_path_never_consults_the_grok_guard(monkeypatch):
    seen = []
    monkeypatch.setattr(mr, "_grok_live_composer_is_typed", lambda *a: seen.append(a) or False)
    _tmux(monkeypatch, "", rc=1)
    mr.stuck_own_message("p", "claude")
    assert seen == []
    mr.stuck_own_message("p", "grok")
    assert seen == [("p", mr.PROMPT_SIGNATURES["grok"])]


# _input_line_typed (the router's typed-text re-read under the pane lock) reads a grok pane with
# grok's own composer reader: only an "empty" live composer lets a paste through.

def _typed_on(monkeypatch, screen, sig=SIG, rc=0):
    monkeypatch.setitem(mr.PROMPT_SIGNATURES, "grok", sig)
    _tmux(monkeypatch, screen, rc=rc)
    return mr._input_line_typed("p", "grok")


def test_grok_typed_line_reads_the_live_composer(monkeypatch):
    assert _typed_on(monkeypatch, _screen("")) is False
    assert _typed_on(monkeypatch, _screen(" [MSG from a | high] hi")) is True


def test_grok_prompt_glyph_only_in_scrollback_holds(monkeypatch):
    # an empty-looking prompt line in scrollback, no grok footer under it: not a composer
    assert _typed_on(monkeypatch, "● x\n❯\n● still working") is True


def test_grok_unverified_signature_holds_even_on_an_empty_composer(monkeypatch):
    assert _typed_on(monkeypatch, _screen(""), sig={"prompt_char": "❯"}) is True


def test_grok_capture_failure_holds(monkeypatch):
    assert _typed_on(monkeypatch, "", rc=1) is True


def test_claude_typed_line_is_unchanged(monkeypatch):
    seen = []
    monkeypatch.setattr(mr, "grok_composer_line_state", lambda *a: seen.append(a) or "empty")
    _tmux(monkeypatch, "❯ \n")
    assert mr._input_line_typed("p", "claude") is False
    _tmux(monkeypatch, "❯ hello\n")
    assert mr._input_line_typed("p", "claude") is True
    assert seen == []


def test_grok_shipped_sentinel_signature_holds(monkeypatch):
    # the shipped grok signature marks itself unverified with a NUL-led prompt sentinel
    assert _typed_on(monkeypatch, _screen(""), sig=mr.PROMPT_SIGNATURES["grok"]) is True


# _grok_is_idle (the delivery gate) reads the composer with the same reader: only an "empty"
# live composer is idle, so the gate and the typed-line re-read under the lock agree.

def _fake_router_idle(monkeypatch, ok=True):
    mod = types.ModuleType("grok_context")
    mod.router_idle = lambda session, signature_verified: (ok and signature_verified, "t")
    monkeypatch.setitem(sys.modules, "grok_context", mod)


def test_grok_idle_gate_reads_the_live_composer(monkeypatch):
    _fake_router_idle(monkeypatch)
    _tmux(monkeypatch, _screen(""))
    assert mr._grok_is_idle("p", SIG) is True
    _tmux(monkeypatch, _screen(" [MSG from a | high] hi"))
    assert mr._grok_is_idle("p", SIG) is False


def test_grok_idle_gate_is_not_fooled_by_a_scrollback_prompt(monkeypatch):
    _fake_router_idle(monkeypatch)
    _tmux(monkeypatch, "● x\n❯\n● still working")
    assert mr._grok_is_idle("p", SIG) is False
