"""Gateway pass-through for the RED ALERT report button (deliverable 6, iOS/watch, G18).

The phone/watch only talk to the gateway (one base, one bearer). POST /red-alert/report
forwards {seat, kind, words} to :8888/api/red-alert/report with channel ios|watch and
returns the API's JSON verbatim; GET /red-alert/reports forwards the ticket list.
"""
import asyncio
import json
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watch_gateway as G  # noqa: E402


class _Req:
    def __init__(self, headers=None, payload=None, query=None):
        self.headers = headers or {}
        self._payload = payload
        self.query = query or {}
        self.match_info = {}

    async def json(self):
        if self._payload is None:
            raise ValueError("bad json")
        return self._payload


def _run(c):
    return asyncio.run(c)


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setattr(G, "gateway_token", lambda: "secret_tok")


def _auth():
    return {"Authorization": "Bearer secret_tok"}


@pytest.fixture
def api(monkeypatch):
    calls = []

    async def fake(method, path, body=None):
        calls.append((method, path, body))
        if method == "POST":
            return 200, {"ok": True, "id": "ra_abc", "card_id": "apr_1", "severity": "bug"}
        return 200, [{"id": "ra_abc", "status": "open"}]

    monkeypatch.setattr(G, "_red_alert_api", fake)
    return calls


def test_report_requires_auth():
    assert _run(G.handle_red_alert_report(_Req(payload={"seat": "gm", "kind": "bug", "words": "x y z"}))).status == 401


def test_report_forwards_with_channel_ios_and_returns_api_json(api):
    resp = _run(G.handle_red_alert_report(_Req(headers=_auth(), payload={"seat": "gm", "kind": "bug", "words": "phone froze"})))
    assert resp.status == 200
    body = json.loads(resp.text)
    assert body["id"] == "ra_abc" and body["card_id"] == "apr_1"
    m, path, sent = api[0]
    assert (m, path) == ("POST", "/api/red-alert/report")
    assert sent == {"seat": "gm", "kind": "bug", "words": "phone froze", "channel": "ios"}


def test_report_channel_watch_is_kept(api):
    _run(G.handle_red_alert_report(_Req(headers=_auth(), payload={"seat": "gm", "kind": "crash", "words": "watch says dead", "channel": "watch"})))
    assert api[0][2]["channel"] == "watch"


def test_report_bad_json_is_400():
    assert _run(G.handle_red_alert_report(_Req(headers=_auth(), payload=None))).status == 400


def test_report_api_down_is_502(monkeypatch):
    async def boom(method, path, body=None):
        raise ConnectionError("refused")
    monkeypatch.setattr(G, "_red_alert_api", boom)
    resp = _run(G.handle_red_alert_report(_Req(headers=_auth(), payload={"seat": "gm", "kind": "bug", "words": "x y z"})))
    assert resp.status == 502 and "api unreachable" in json.loads(resp.text)["error"]


def test_reports_list_forwards_status(api):
    resp = _run(G.handle_red_alert_reports(_Req(headers=_auth(), query={"status": "open"})))
    assert resp.status == 200 and json.loads(resp.text)[0]["id"] == "ra_abc"
    assert api[0][:2] == ("GET", "/api/red-alert/reports?status=open")


def test_routes_registered():
    src = open(G.__file__).read()
    assert 'add_post("/red-alert/report", handle_red_alert_report)' in src
    assert 'add_get("/red-alert/reports", handle_red_alert_reports)' in src


def test_red_alert_api_has_its_aiohttp_import():
    """The pass-through used aiohttp without importing it: every real phone call got
    502 'name aiohttp is not defined' while the fake-runner tests stayed green."""
    import inspect
    src = inspect.getsource(G._red_alert_api)
    assert "import aiohttp" in src
