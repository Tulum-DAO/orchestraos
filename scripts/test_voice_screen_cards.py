"""The voice brain gets the cards on screen during a call: the call socket's context frame carries `cards`, the relay
bridge hands them to the gateway, the gateway keeps them (clipped by services/arturo/screen_cards.pick_cards, the
same rules as a text turn) in the web slot of active-surface.json, and the screen readout and read_screen_context
add the same cards block a text turn gets. A stand-in screen_cards module where the real one is not in this tree;
temp files only."""
import importlib.util
import json
import sys
import time
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

CARDS = {"v": 1, "focus": "c1", "cards": [
    {"ref": "c1", "kind": "decision", "from": "pm-demo", "state": "waiting", "surface": "approvals",
     "question": "Which size?", "options": ["A. Small", "B. Large"], "secret_field": "dropped"},  # pragma: allowlist secret
    {"ref": "c2", "line": "Approval from dev-demo, waiting: Deploy the drawer?"}]}


def _real_screen_cards():
    try:
        import services.arturo.screen_cards as m      # noqa: F401
        return m
    except Exception:
        return None


@pytest.fixture
def fake_cards(monkeypatch):
    """A stand-in with the real module's two entry points (pick_cards keeps known fields only)."""
    m = types.ModuleType("services.arturo.screen_cards")
    m.calls = []

    def pick_cards(raw):
        m.calls.append(raw)
        if not isinstance(raw, dict) or not isinstance(raw.get("cards"), list):
            return None
        keep = ("ref", "line", "kind", "from", "state", "surface", "question", "options")
        return {"v": 1, "focus": raw.get("focus"),
                "cards": [{k: v for k, v in c.items() if k in keep} for c in raw["cards"]]}

    m.pick_cards = pick_cards
    m.cards_block = lambda cards: "CARDS ON THE OPERATOR'S SCREEN NOW: " + ", ".join(
        c.get("question") or c.get("line") for c in cards["cards"])
    import services.arturo as pkg
    monkeypatch.setitem(sys.modules, "services.arturo.screen_cards", m)
    monkeypatch.setattr(pkg, "screen_cards", m, raising=False)
    return m


@pytest.fixture
def G(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCH_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("watch_gateway_cards", HERE / "watch_gateway.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    monkeypatch.setattr(g, "SURFACE_PATH", tmp_path / "active-surface.json")
    monkeypatch.setattr(g, "VOICE_CALLS_DIR", tmp_path / "voice-calls")
    return g


def _web(G):
    return json.loads(G.SURFACE_PATH.read_text())["devices"]["web"]


def test_gateway_keeps_the_clipped_cards_in_the_web_slot(G, fake_cards):
    assert G._record_web_call_surface("/approvals", "approvals:x", "conv_sample1", cards=CARDS)
    web = _web(G)
    assert web["cards"]["focus"] == "c1" and len(web["cards"]["cards"]) == 2
    assert "secret_field" not in web["cards"]["cards"][0]             # only what pick_cards keeps
    assert json.loads(G.SURFACE_PATH.read_text())["current"]["cards"] == web["cards"]


def test_no_cards_or_a_bad_record_leaves_the_slot_without_cards(G, fake_cards):
    G._record_web_call_surface("/approvals", None, cards=CARDS)
    G._record_web_call_surface("/approvals", None)                     # the next frame has none
    assert "cards" not in _web(G)
    G._record_web_call_surface("/approvals", None, cards={"cards": "nope"})
    assert "cards" not in _web(G)
    G._record_web_call_surface("/approvals", None, cards={"v": 1, "cards": []})
    assert "cards" not in _web(G)


def test_without_the_reader_the_page_still_goes_through(G, monkeypatch):
    monkeypatch.setitem(sys.modules, "services.arturo.screen_cards", None)   # import fails
    assert G._record_web_call_surface("/approvals", None, cards=CARDS)
    assert _web(G)["route"] == "/approvals" and "cards" not in _web(G)


def test_relay_bridge_passes_cards_only_when_the_frame_has_them(monkeypatch):
    sys.modules.setdefault("numpy", types.ModuleType("numpy"))
    from services.arturo import relay_live_bridge as B
    got = []
    s = B.RelayLiveSession(None, "http://x", {}, on_surface=lambda *a, **k: got.append((a, k)))
    s._note_page(json.dumps({"route": "/approvals", "focusedEntity": "approvals:x", "cards": CARDS}))
    s._note_page(json.dumps({"event": "context", "route": "/agents"}))
    assert got[0][1] == {"cards": CARDS} and got[0][0][:2] == ("/approvals", "approvals:x")
    assert got[1][1] == {} and got[1][0][0] == "/agents"               # a three-argument call, as before
    s._stamp_page()                                                     # the periodic re-stamp
    assert got[2][1] == {}


def _surface(tmp_path, cards, age=5):
    p = tmp_path / "active-surface.json"
    cur = {"route": "/approvals", "hint": None, "device": "web", "updated_at": time.time() - age}
    if cards:
        cur["cards"] = cards
    p.write_text(json.dumps({"devices": {"web": cur}, "current": cur}))
    return p


def test_readout_adds_the_cards_block(tmp_path, fake_cards):
    from services.arturo import surface
    line = surface.readout_line(_surface(tmp_path, CARDS))
    assert line.startswith("OPERATOR'S SCREEN: /approvals")
    assert "CARDS ON THE OPERATOR'S SCREEN NOW: Which size?" in line
    assert "\n" not in surface.readout_line(_surface(tmp_path, None))   # no cards: the one line as before
    stale = surface.readout_line(_surface(tmp_path, CARDS, age=surface.STALE_S + 30))
    assert "CARDS" not in stale and "backgrounded" in stale


def test_read_screen_context_adds_the_cards_block(tmp_path, fake_cards):
    from services.arturo import surface
    out = surface.resolve_screen(_surface(tmp_path, CARDS), gw_get=lambda path: None)
    assert "CARDS ON THE OPERATOR'S SCREEN NOW" in out
    stale = surface.resolve_screen(_surface(tmp_path, CARDS, age=surface.STALE_S + 30), gw_get=lambda path: None)
    assert "CARDS" not in stale


@pytest.mark.skipif(_real_screen_cards() is None, reason="services/arturo/screen_cards.py is not in this tree")
def test_with_the_real_reader_the_limits_hold(G):
    long = {"v": 1, "focus": "c1", "cards": [{"ref": "c1", "kind": "decision", "question": "x" * 900}]
            + [{"ref": f"c{i}", "line": "y"} for i in range(2, 30)]}
    G._record_web_call_surface("/approvals", None, cards=long)
    cards = _web(G)["cards"]
    assert len(cards["cards"]) <= 12 and len(cards["cards"][0]["question"]) <= 300
