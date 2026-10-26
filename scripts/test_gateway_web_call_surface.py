"""A dashboard call's page ({route, focusedEntity}) becomes the `web` device's surface, which the voice brain's
screen readout and read_screen_context read."""
import json

import scripts.watch_gateway as G
from services.arturo import surface


def _paths(tmp_path, monkeypatch):
    sp = tmp_path / "arturo" / "active-surface.json"
    monkeypatch.setattr(G, "SURFACE_PATH", sp)
    monkeypatch.setattr(G, "VOICE_CALLS_DIR", tmp_path / "voice-calls")
    return sp


def test_the_call_page_is_the_web_surface(tmp_path, monkeypatch):
    sp = _paths(tmp_path, monkeypatch)
    assert G._record_web_call_surface("/agents", "agents:ops-1", "00000000-0000-4000-8000-0000000000bb")
    cur = json.loads(sp.read_text())["current"]
    assert cur["device"] == "web" and cur["route"] == "/agents"
    assert cur["focused"] == {"kind": "agent", "id": "ops-1"}        # the route's plural kind, normalized
    assert 'OPERATOR\'S SCREEN: /agents — "viewing agent ops-1"' in surface.readout_line(sp)
    assert list((tmp_path / "voice-calls").glob("vc_client_*.surface.jsonl"))    # the call's sidecar


def test_the_screen_tool_resolves_an_overlay_agent_on_a_bare_route(tmp_path, monkeypatch):
    sp = _paths(tmp_path, monkeypatch)
    G._record_web_call_surface("/agents", "agent:ops-1")
    seen = []
    out = surface.resolve_screen(sp, gw_get=lambda p: seen.append(p) or None, focus_path=tmp_path / "none.json")
    assert "agent 'ops-1'" in out and any("session=ops-1" in p for p in seen)


def test_a_bad_page_is_ignored(tmp_path, monkeypatch):
    sp = _paths(tmp_path, monkeypatch)
    assert not G._record_web_call_surface("agents", None)
    assert not G._record_web_call_surface(None, None)
    assert not sp.exists()
    G._record_web_call_surface("/", "garbage")
    assert json.loads(sp.read_text())["current"]["focused"] is None
