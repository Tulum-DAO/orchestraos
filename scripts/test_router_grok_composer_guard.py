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
