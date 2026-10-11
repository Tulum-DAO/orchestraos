"""R2: watch_gateway.verified_inject refuses to type into a grok pane whose composer
signature is not verified. Drives the REAL verified_inject with a fake detector + fake tmux, the
test_gateway_send_gate_composer.py harness. Nothing touches a real pane.
"""
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def G(monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(ROOT))
    monkeypatch.setattr(sys, "path", [str(ROOT / "scripts"), str(ROOT)] + list(sys.path))
    spec = importlib.util.spec_from_file_location("watch_gateway_grok_r2",
                                                  ROOT / "scripts" / "watch_gateway.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.__file__.startswith(str(ROOT))
    return m


IDLE_PANE = "history line\n\u276f \n"            # a ❯ composer: what _is_prompt_line accepts


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


def _env(G, monkeypatch, row, captures=(IDLE_PANE, IDLE_PANE, IDLE_PANE)):
    t = _Tmux(captures)
    monkeypatch.setattr(G, "_tmux", t)
    monkeypatch.setattr(G, "INJECT_INGEST_WAIT_S", 0.0)
    monkeypatch.setattr(G, "_agent_status", lambda: _Det(row))
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    return t


def _typed(t):
    return [c for c in t.calls if c[0] == "send-keys"]


GROK_IDLE = {"state": "idle", "activity": "Grok turn ended", "runtime": "grok",
             "process": {"runtime": "grok"}}


def test_gateway_refuses_grok_with_an_unverified_composer(G, monkeypatch):
    """Delete the R2 guard in verified_inject and this goes RED (the ❯ line passes
    _pane_has_composer and the text is typed)."""
    t = _env(G, monkeypatch, GROK_IDLE)
    ok, info = G.verified_inject("zz-grok", "hello")
    assert ok is False
    assert info["reason"] == "unverified-composer"
    assert _typed(t) == [], "nothing may be typed into an unmeasured composer"


def test_force_does_not_bypass_the_unverified_composer_guard(G, monkeypatch):
    t = _env(G, monkeypatch, GROK_IDLE)
    ok, info = G.verified_inject("zz-grok", "hello", force=True)
    assert (ok, info["reason"]) == (False, "unverified-composer")
    assert _typed(t) == []


def test_runtime_from_the_process_block_is_enough(G, monkeypatch):
    row = {"state": "working", "activity": "", "process": {"runtime": "grok"}}
    _env(G, monkeypatch, row)
    assert G.verified_inject("zz-grok", "hi")[1]["reason"] == "unverified-composer"


def test_guard_fails_closed_when_the_signature_table_is_unreadable(G, monkeypatch):
    import builtins
    real = builtins.__import__

    def broken(name, *a, **k):
        if name == "runtime_signatures":
            raise ImportError("simulated")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", broken)
    assert G._composer_signature_verified("grok") is False


def test_guard_is_the_only_thing_that_changed_for_a_verified_signature(G, monkeypatch):
    """Control: once the signature is verified, the guard steps aside and the
    normal gates run (the ❯ pane is injected). Proves the refusal above IS the guard."""
    monkeypatch.setattr(G, "_composer_signature_verified", lambda rt: True)
    t = _env(G, monkeypatch, GROK_IDLE, captures=(IDLE_PANE, IDLE_PANE, IDLE_PANE, IDLE_PANE))
    ok, info = G.verified_inject("zz-grok", "hello")
    assert info.get("reason") != "unverified-composer"
    assert any("-l" in c for c in _typed(t))


def test_non_grok_seats_are_untouched(G, monkeypatch):
    row = {"state": "idle", "activity": "", "process": {"runtime": "claude"}}
    t = _env(G, monkeypatch, row, captures=(IDLE_PANE, IDLE_PANE, IDLE_PANE, IDLE_PANE))
    ok, info = G.verified_inject("c1", "hello")
    assert info.get("reason") != "unverified-composer"
    assert any("-l" in c for c in _typed(t))


def test_real_signature_table_says_grok_is_unverified_today():
    spec = importlib.util.spec_from_file_location("rs_r2", ROOT / "scripts" / "runtime_signatures.py")
    rs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rs)
    assert rs.signature_verified("grok") is False
    assert rs.signature_verified("claude") is True
