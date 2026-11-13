"""Grok gateway readiness: once a grok composer signature IS verified, the gateway still types into
a grok pane only through its own gate, _grok_gateway_ready (the private fleet's rule, measured on a
fenced grok 1.0.50 seat): the gateway arm is on (default on; GATEWAY_GROK_INJECT_ARMED=0 or the
~/runtime/GATEWAY_GROK_INJECT_DISARMED sentinel switches it off, the sentinel live without a
restart), the seat's own events.jsonl says the turn is idle or working (grok queues typed input
mid-turn like Claude; 'unknown' is refused), the visible ANSI composer classifies as an EMPTY grok
prompt, and that prompt's footer is the bottom line of the screen (grok redraws its footer in place,
so a ❯+footer pair higher up is agent output quoting a screen, not the live composer).

Everything else refuses with the same "unverified-composer" reason callers already handle, before
any other gate and regardless of force. Nothing here touches a real pane: the test_grok_gateway_r2
harness (fake detector + fake tmux) drives the REAL verified_inject.
"""
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture
def G(monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(ROOT))
    monkeypatch.setattr(sys, "path", [str(ROOT / "scripts"), str(ROOT)] + list(sys.path))
    spec = importlib.util.spec_from_file_location("watch_gateway_grok_ready",
                                                  ROOT / "scripts" / "watch_gateway.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.__file__.startswith(str(ROOT))
    return m


@pytest.fixture
def gc(monkeypatch, tmp_path):
    """grok_context with HOME fenced (the disarm sentinel lives under ~/runtime)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("GATEWAY_GROK_INJECT_ARMED", raising=False)
    import grok_context
    return grok_context


# A verified grok signature for these tests: the real one is unverified (R2), and the gate must
# work for an install whose canary verified its own.
VERIFIED_GROK_SIG = {"prompt_char": "❯", "has_ghost_suggestions": False,
                     "idle_footer_re": r"grok-1\.0 .*ctx", "verified": True,
                     "idle_source": "events.jsonl"}
FOOTER = "  grok-1.0 · 12% ctx"
EMPTY_COMPOSER = "some history\n❯ \n" + FOOTER + "\n"
TYPED_COMPOSER = "some history\n❯ hello there\n" + FOOTER + "\n"
QUOTED_SCREEN = "❯ \n" + FOOTER + "\n\nthe agent printed the screen above\n"   # footer not bottom


class _Det:
    def __init__(self, row): self._r = row
    def get_agent_status(self, sess): return self._r


class _Tmux:
    def __init__(self, captures):
        self.calls, self._c = [], list(captures)

    def __call__(self, *args, timeout=5):
        self.calls.append(args)
        out = self._c.pop(0) if (args[0] == "capture-pane" and self._c) else ""
        return types.SimpleNamespace(returncode=0, stdout=out)


GROK_IDLE = {"state": "idle", "activity": "Grok turn ended", "runtime": "grok",
             "process": {"runtime": "grok"}}


def _ready_env(G, monkeypatch, *, turn="idle", capture=EMPTY_COMPOSER, armed=True, verified=True):
    """Everything the gate needs, each knob overridable; returns the fake tmux."""
    import runtime_signatures as rs
    monkeypatch.setitem(rs.PROMPT_SIGNATURES, "grok", dict(VERIFIED_GROK_SIG, verified=verified))
    monkeypatch.setattr(G, "_composer_signature_verified", lambda rt: verified)
    import grok_context as gc
    monkeypatch.setattr(gc, "gateway_inject_armed", lambda: armed)
    monkeypatch.setattr(gc, "seat_turn_state", lambda s, **k: {"state": turn, "sid": "s1"})
    t = _Tmux([capture] * 6)
    monkeypatch.setattr(G, "_tmux", t)
    monkeypatch.setattr(G, "INJECT_INGEST_WAIT_S", 0.0)
    monkeypatch.setattr(G, "_agent_status", lambda: _Det(GROK_IDLE))
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    return t


def _typed(t):
    return [c for c in t.calls if c[0] == "send-keys"]


# ------------------------------------------------------------------ the arm (grok_context)

def test_gateway_inject_is_armed_by_default(gc):
    assert gc.gateway_inject_armed() is True


def test_env_0_disarms_at_start(gc, monkeypatch):
    monkeypatch.setenv("GATEWAY_GROK_INJECT_ARMED", "0")
    assert gc.gateway_inject_armed() is False


def test_the_sentinel_disarms_live_without_a_restart(gc, tmp_path):
    sentinel = tmp_path / "home" / "runtime" / "GATEWAY_GROK_INJECT_DISARMED"
    assert gc.gateway_inject_armed() is True
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("")
    assert gc.gateway_inject_armed() is False
    sentinel.unlink()
    assert gc.gateway_inject_armed() is True


def test_the_router_arm_and_the_gateway_arm_are_separate(gc, monkeypatch):
    monkeypatch.setenv("ROUTER_GROK_DELIVERY_ARMED", "1")
    monkeypatch.setenv("GATEWAY_GROK_INJECT_ARMED", "0")
    assert gc.delivery_armed() is True and gc.gateway_inject_armed() is False


# ------------------------------------------------------------------ the gate (_grok_gateway_ready)

def test_ready_when_verified_armed_idle_and_the_composer_is_an_empty_prompt_with_the_footer_at_the_bottom(G, monkeypatch):
    _ready_env(G, monkeypatch)
    assert G._grok_gateway_ready("zz-grok") == (True, "grok-gateway-ready")


def test_working_is_a_typeable_turn_state_unknown_is_not(G, monkeypatch):
    _ready_env(G, monkeypatch, turn="working")
    assert G._grok_gateway_ready("zz-grok")[0] is True
    _ready_env(G, monkeypatch, turn="unknown")
    ok, why = G._grok_gateway_ready("zz-grok")
    assert ok is False and "unknown" in why
    assert G.GROK_INJECT_TURN_STATES == frozenset({"idle", "working"})


def test_an_unverified_signature_refuses_before_anything_else(G, monkeypatch):
    _ready_env(G, monkeypatch, verified=False)
    ok, why = G._grok_gateway_ready("zz-grok")
    assert ok is False and "not" in why and "verified" in why


def test_a_switched_off_arm_refuses(G, monkeypatch):
    _ready_env(G, monkeypatch, armed=False)
    ok, why = G._grok_gateway_ready("zz-grok")
    assert ok is False and "switched off" in why


def test_a_typed_composer_refuses(G, monkeypatch):
    _ready_env(G, monkeypatch, capture=TYPED_COMPOSER)
    ok, why = G._grok_gateway_ready("zz-grok")
    assert ok is False and "typed" in why


def test_a_quoted_screen_whose_footer_is_not_the_bottom_line_refuses(G, monkeypatch):
    """The classifier takes the last ❯+footer pair anywhere; only a footer on the bottom line is the
    live composer (grok redraws its footer in place: one footer per pane)."""
    _ready_env(G, monkeypatch, capture=QUOTED_SCREEN)
    ok, why = G._grok_gateway_ready("zz-grok")
    assert ok is False and "bottom" in why


def test_a_failed_capture_refuses(G, monkeypatch):
    _ready_env(G, monkeypatch)
    monkeypatch.setattr(G, "_capture_pane", lambda *a, **k: None)
    ok, why = G._grok_gateway_ready("zz-grok")
    assert ok is False and "capture failed" in why


def test_an_unreadable_events_log_refuses(G, monkeypatch):
    _ready_env(G, monkeypatch)
    import grok_context as gc
    monkeypatch.setattr(gc, "seat_turn_state", lambda s, **k: (_ for _ in ()).throw(OSError("gone")))
    ok, why = G._grok_gateway_ready("zz-grok")
    assert ok is False and "unreadable" in why


# ------------------------------------------------------------------ verified_inject consults it

def test_verified_inject_types_into_a_ready_grok_pane(G, monkeypatch):
    """Control for the refusals below: a ready gate steps aside and the normal gates run. The gate
    is held open here (its own knobs are tested above); the captures are the plain ❯ pane the normal
    composer gates accept."""
    t = _ready_env(G, monkeypatch, capture="history line\n\u276f \n")
    monkeypatch.setattr(G, "_grok_gateway_ready", lambda s: (True, "grok-gateway-ready"))
    ok, info = G.verified_inject("zz-grok", "hello")
    assert info.get("reason") != "unverified-composer"
    assert any("-l" in c for c in _typed(t))


@pytest.mark.parametrize("knob", [dict(armed=False), dict(turn="unknown"), dict(capture=TYPED_COMPOSER),
                                  dict(capture=QUOTED_SCREEN)])
def test_verified_inject_refuses_a_not_ready_grok_pane_before_any_other_gate_regardless_of_force(G, monkeypatch, knob):
    t = _ready_env(G, monkeypatch, **knob)
    for force in (False, True):
        ok, info = G.verified_inject("zz-grok", "hello", force=force)
        assert (ok, info["reason"], info["runtime"]) == (False, "unverified-composer", "grok")
        assert info["activity"]                                   # the gate's why, for the caller
    assert _typed(t) == []


def test_non_grok_seats_never_consult_the_gate(G, monkeypatch):
    t = _ready_env(G, monkeypatch, armed=False)
    monkeypatch.setattr(G, "_agent_status", lambda: _Det({"state": "idle", "activity": "", "process": {"runtime": "claude"}}))
    monkeypatch.setattr(G, "_grok_gateway_ready", lambda s: (_ for _ in ()).throw(AssertionError("consulted")))
    ok, info = G.verified_inject("c1", "hello")
    assert info.get("reason") != "unverified-composer"
