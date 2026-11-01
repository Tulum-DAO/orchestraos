"""Arturo's inject_message to a headless seat goes through the gateway's /agent-message (its pane holds the seat's
supervisor); a tmux seat keeps the two-phase pane path (test_inject_invariant.py). Stand-ins only: no gateway,
no tmux."""
import importlib.util
import pathlib

import pytest

from services.arturo.conftest import as_fleet
from scripts import gateway_deliver as GD


def _load_proxy():
    spec = importlib.util.spec_from_file_location("arturo_proxy_hl", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return as_fleet(mod)


def _r(**kw):
    out = {"ok": False, "delivered": False, "held": False, "status": 200, "reason": "", "body": {}}
    out.update(kw)
    return out


@pytest.fixture
def proxy(monkeypatch):
    mod = _load_proxy()
    monkeypatch.setattr(GD, "headless_seat", lambda s, orch_dir=None: s if s == "dev-demo" else None)
    mod._run_on_machine = lambda *a, **k: pytest.fail(f"pane touched for a headless seat: {a[1]}")
    return mod


def test_a_headless_seat_gets_the_words_through_the_gateway(proxy, monkeypatch):
    sent = []
    monkeypatch.setattr(GD, "deliver", lambda *a, **k: sent.append((a, k)) or _r(ok=True, delivered=True))
    out = proxy.execute_tool("inject_message", {"session_name": "dev-demo", "message": "run the sweep"})
    assert out == "Message injected into 'dev-demo' on vps (verified landed)."
    (args, kw), = sent
    assert args == ("dev-demo", "[Arturo Voice]: run the sweep")          # attributed, as on the pane path
    assert kw["accepts_held"] is True and kw["source"] == "mail"


def test_mid_turn_is_queued_and_said_so(proxy, monkeypatch):
    monkeypatch.setattr(GD, "deliver", lambda *a, **k: _r(ok=True, held=True))
    out = proxy.execute_tool("inject_message", {"session_name": "dev-demo", "message": "x"})
    assert "queued" in out and "not delivered yet" in out


def test_a_refusal_is_said_plainly(proxy, monkeypatch):
    monkeypatch.setattr(GD, "deliver", lambda *a, **k: _r(status=409, reason="busy"))
    out = proxy.execute_tool("inject_message", {"session_name": "dev-demo", "message": "x"})
    assert out.startswith("Failed to deliver to 'dev-demo' (busy)") and "did not go through" in out


def test_a_tmux_seat_keeps_the_pane_path(monkeypatch):
    mod = _load_proxy()
    monkeypatch.setattr(GD, "headless_seat", lambda s, orch_dir=None: None)
    monkeypatch.setattr(GD, "deliver", lambda *a, **k: pytest.fail("a tmux seat went to the gateway"))
    cmds = []
    mod._run_on_machine = lambda mac, vps, timeout=15, prefer_mac=False: (
        cmds.append(vps) or (True, "\n---\n> \n---\n", "vps"))
    assert "verified landed" in mod.execute_tool("inject_message", {"session_name": "pm-demo", "message": "x"})
    assert any("send-keys" in c for c in cmds)
