"""GET /away, the /presence foreground signal, and the operator-actions ledger, through the REAL app (scope
middleware, real DeviceStore). RFC 0002; gm msg_8a661e3c + msg_71cd9caa conditions."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scripts.watch_gateway as G  # noqa: E402
from scripts.device_tokens import DeviceStore  # noqa: E402

FLEET = "fleet-bearer-0123456789abcdef-for-tests"


@pytest.fixture
def box(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    tok = tmp_path / "gateway-token"
    tok.write_text(FLEET)
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    monkeypatch.setattr(G, "ORCH_DIR", data)
    monkeypatch.setattr(G, "TOKEN_FILE", tok)
    monkeypatch.setitem(G._agents_cache, "data", [{"id": "gm", "state": "working"}])
    monkeypatch.setattr(G, "_tmux_session_names", lambda: {"gm"})
    monkeypatch.setattr(G, "_protected_refusal", lambda request, session: None)
    store = DeviceStore(data / "state" / "devices")
    dev_id, dev_tok = store.mint("iPad", ["read", "message"])
    return {"data": data, "dev_id": dev_id, "dev": dev_tok, "fleet": FLEET}


def _call(box, method, path, token="fleet", body=None, headers=None, params=None):
    async def go():
        async with TestClient(TestServer(G.build_app())) as c:
            h = dict(headers or {})
            if token:
                h["Authorization"] = f"Bearer {box[token]}"
            r = await c.request(method, path, json=body, headers=h, params=params)
            try:
                return r.status, await r.json()
            except Exception:
                return r.status, None
    return asyncio.run(go())


def _ledger(box):
    p = box["data"] / "state" / "operator-actions.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


# ---- /away ------------------------------------------------------------------------------------------

def test_away_is_read_scoped_and_refuses_anonymous(box):
    assert _call(box, "GET", "/away", token=None)[0] == 401
    status, body = _call(box, "GET", "/away", token="dev")
    assert status == 200 and body["ok"] and body["anchor"] is None and body["reason"] == "no_action_yet"
    assert G.ROUTE_SCOPES[("GET", "/away")] == "read"


def test_away_rejects_an_unknown_anchor_mode(box):
    assert _call(box, "GET", "/away", params={"anchor": "everything"})[0] == 400


# ---- the ledger: delivered or held only ---------------------------------------------------------------

def test_a_delivered_send_from_a_device_is_the_anchor_with_its_device(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", lambda s, t, f=False: (True, {"verified": True}))
    status, _ = _call(box, "POST", "/agent-message", token="dev", body={"session": "gm", "text": "fix the card"},
                      headers={"X-Surface": "phone"})
    assert status == 200
    [row] = _ledger(box)
    assert row["device"] == f"{box['dev_id']}:iPad" and row["said"] == "fix the card" and row["surface"] == "phone"
    status, body = _call(box, "GET", "/away")
    a = body["anchor"]
    assert (a["kind"], a["device"], a["device_source"], a["said"], a["target"]) == \
        ("message", "iPad", "answer_device", "fix the card", "gm")
    assert a["outcome"]["state"] == "working"


def test_the_fleet_bearer_stamps_no_device_and_never_the_token(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", lambda s, t, f=False: (True, {}))
    _call(box, "POST", "/agent-message", body={"session": "gm", "text": "hi"})
    [row] = _ledger(box)
    assert row["device"] is None
    assert FLEET not in (box["data"] / "state" / "operator-actions.jsonl").read_text()


def test_a_refused_send_writes_nothing(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", lambda s, t, f=False: (False, {"reason": "unverified"}))
    status, _ = _call(box, "POST", "/agent-message", body={"session": "gm", "text": "lost"})
    assert status == 502 and _ledger(box) == []


def test_a_busy_send_without_hold_writes_nothing(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", lambda s, t, f=False: (False, {"reason": "busy", "state": "working"}))
    status, _ = _call(box, "POST", "/agent-message", body={"session": "gm", "text": "later"})
    assert status == 409 and _ledger(box) == []


def test_a_held_send_is_recorded(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", lambda s, t, f=False: (False, {"reason": "busy", "state": "working"}))
    monkeypatch.setattr(G, "_hold_for_boundary", lambda *a, **k: "msg_held")
    status, body = _call(box, "POST", "/agent-message",
                         body={"session": "gm", "text": "when you're free", "accepts": ["held"]})
    assert status == 200 and body["held"] and [r["said"] for r in _ledger(box)] == ["when you're free"]


# ---- /presence --------------------------------------------------------------------------------------

def _focus(box):
    p = box["data"] / "state" / "operator-presence.json"
    return json.loads(p.read_text()) if p.exists() else {}


def test_a_body_without_viewing_leaves_the_focus_unchanged(box):
    _call(box, "POST", "/presence", body={"viewing": "dev-seat"})
    before = _focus(box)
    assert before.get("viewing") == "dev-seat"
    status, _ = _call(box, "POST", "/presence", token="dev", body={"foreground": True})
    assert status == 200 and _focus(box) == before


def test_viewing_null_still_clears_the_focus(box):
    _call(box, "POST", "/presence", body={"viewing": "dev-seat"})
    _call(box, "POST", "/presence", body={"viewing": None})
    assert "viewing" not in _focus(box)


def test_foreground_is_stored_in_its_own_file_keyed_by_device_or_web_client(box):
    _call(box, "POST", "/presence", token="dev", body={"foreground": True}, headers={"X-Surface": "phone"})
    _call(box, "POST", "/presence", body={"foreground": True, "surface": "web", "client_id": "c-123",
                                          "label": "Chrome on macOS"})
    pres = json.loads((box["data"] / "state" / "away-presence.json").read_text())
    assert set(pres) == {f"device:{box['dev_id']}", "web:c-123"}
    assert pres[f"device:{box['dev_id']}"]["label"] == "iPad"
    assert pres["web:c-123"]["label"] == "Chrome on macOS"
    assert not (box["data"] / "state" / "operator-presence.json").exists()
    status, body = _call(box, "GET", "/away", params={"anchor": "screen"})
    assert body["anchor"]["kind"] == "screen"
