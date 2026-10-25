"""Gateway pass-through for New Agent from a paired device (iOS/macOS "+", the web form's #392/#413).

The phone only talks to the gateway (one base, one bearer). Four routes forward to the API's own
(api/src/routes/agents-new.ts, runtimes-available.ts) and return its JSON verbatim. The gateway
decides WHO may ask (create + probe: admin and a person's surface, never an automation) and what a
DEVICE may create: a worker under a registered parent. No orphan seat, no GM from a device.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watch_gateway as G  # noqa: E402
from scripts.test_gateway_scopes import _call, _with_store  # noqa: E402


class _Req:
    def __init__(self, headers=None, payload=None):
        self.headers = headers or {}
        self._payload = payload
        self.query = {}
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


def _auth(actor="app:ios"):
    h = {"Authorization": "Bearer secret_tok"}
    if actor:
        h["X-Actor"] = actor
    return h


@pytest.fixture
def api(monkeypatch):
    calls = []
    answers = {}

    async def fake(method, path, body=None):
        calls.append((method, path, body))
        return answers.get((method, path), (200, {"ok": True}))

    monkeypatch.setattr(G, "_new_agent_api", fake)
    return calls, answers


def _body(resp):
    import json
    return json.loads(resp.body)


GOOD = {"name": "scratch-1", "task": "say hi", "runtime": "claude", "role": "tester", "parent": "gm"}


def test_create_forwards_the_fields_and_returns_the_api_answer_verbatim(api):
    calls, answers = api
    answers[("POST", "/api/agents/new")] = (200, {"ok": True, "id": "scratch-1", "session": "scratch-1",
                                                 "runtime": "claude", "parent": "gm"})
    r = _run(G.handle_agents_new(_Req(_auth(), dict(GOOD, extra="dropped", gm=False))))
    assert r.status == 200 and _body(r)["session"] == "scratch-1"
    assert calls == [("POST", "/api/agents/new", {"name": "scratch-1", "task": "say hi", "runtime": "claude",
                                                  "role": "tester", "parent": "gm"})]


@pytest.mark.parametrize("status,payload", [
    (400, {"ok": False, "reason": "unknown_parent", "parent": "nobody"}),
    (409, {"ok": False, "reason": "name_taken", "name": "scratch-1"}),
    (409, {"ok": False, "reason": "no_authed_runtime", "runtimes": []}),
    (502, {"ok": False, "reason": "spawn_failed", "detail": "x"}),
])
def test_an_api_refusal_comes_back_with_its_status_and_reason(api, status, payload):
    calls, answers = api
    answers[("POST", "/api/agents/new")] = (status, payload)
    r = _run(G.handle_agents_new(_Req(_auth(), GOOD)))
    assert r.status == status and _body(r) == payload


@pytest.mark.parametrize("parent", [None, "", "   ", 7])
def test_a_device_must_name_a_parent_no_orphan_seat(api, parent):
    calls, _ = api
    body = dict(GOOD)
    body.pop("parent")
    if parent is not None:
        body["parent"] = parent
    r = _run(G.handle_agents_new(_Req(_auth(), body)))
    assert r.status == 400 and _body(r)["reason"] == "parent_required" and calls == []


def test_a_device_never_creates_a_general_manager(api):
    calls, _ = api
    r = _run(G.handle_agents_new(_Req(_auth(), dict(GOOD, gm=True))))
    assert r.status == 400 and _body(r)["reason"] == "gm_not_from_device" and calls == []


@pytest.mark.parametrize("headers,status", [
    ({}, 401),                                                       # no bearer
    ({"Authorization": "Bearer secret_tok"}, 403),                   # the bare fleet token
    ({"Authorization": "Bearer secret_tok", "X-Actor": "automation:cron"}, 403),
])
def test_create_and_refresh_are_a_persons_act(api, headers, status):
    calls, _ = api
    assert _run(G.handle_agents_new(_Req(headers, GOOD))).status == status
    assert _run(G.handle_runtimes_available_refresh(_Req(headers))).status == status
    assert calls == []


@pytest.mark.parametrize("actor", ["app:ios", "app:mac", "operator", "interactive:shaw"])
def test_a_persons_surface_may_create(api, actor):
    calls, _ = api
    assert _run(G.handle_agents_new(_Req(_auth(actor), GOOD))).status == 200 and len(calls) == 1


def test_bad_json_is_400_and_nothing_is_forwarded(api):
    calls, _ = api
    assert _run(G.handle_agents_new(_Req(_auth(), None))).status == 400
    assert _run(G.handle_agents_new(_Req(_auth(), ["x"]))).status == 400
    assert calls == []


def test_the_reads_and_the_refresh_forward_to_the_api_paths(api):
    calls, answers = api
    answers[("GET", "/api/runtimes/available")] = (200, {"providers": [{"id": "claude", "installed": True,
                                                                         "authed": True}]})
    answers[("GET", "/api/agents/new/parents")] = (200, {"agents": [{"id": "gm", "tier": "T0"}]})
    r = _run(G.handle_runtimes_available(_Req(_auth(None))))
    assert r.status == 200 and _body(r)["providers"][0]["id"] == "claude"
    assert _run(G.handle_agents_new_parents(_Req(_auth(None)))).status == 200
    assert _run(G.handle_runtimes_available_refresh(_Req(_auth()))).status == 200
    assert calls == [("GET", "/api/runtimes/available", None), ("GET", "/api/agents/new/parents", None),
                     ("POST", "/api/runtimes/available/refresh", None)]


def test_the_reads_need_a_bearer():
    assert _run(G.handle_runtimes_available(_Req())).status == 401
    assert _run(G.handle_agents_new_parents(_Req())).status == 401


def test_an_unreachable_api_is_502(monkeypatch):
    async def boom(method, path, body=None):
        raise OSError("connection refused")
    monkeypatch.setattr(G, "_new_agent_api", boom)
    r = _run(G.handle_agents_new(_Req(_auth(), GOOD)))
    assert r.status == 502 and "api unreachable" in _body(r)["error"]


# ---------------------------------------------------------------- the scope table (server side)
def test_the_four_routes_are_declared_and_registered():
    table = G.ROUTE_SCOPES
    assert table[("POST", "/agents/new")] == "admin"
    assert table[("GET", "/agents/new/parents")] == "admin"      # it names every registered seat
    assert table[("POST", "/runtimes/available/refresh")] == "admin"
    assert table[("GET", "/runtimes/available")] == "read"
    app = G.build_app()
    routes = {(r.method, r.resource.canonical) for r in app.router.routes() if r.resource is not None}
    for key in [("POST", "/agents/new"), ("GET", "/agents/new/parents"),
                ("POST", "/runtimes/available/refresh"), ("GET", "/runtimes/available")]:
        assert key in routes


def test_a_read_message_device_cannot_create_or_list_parents(monkeypatch, tmp_path):
    _, _, tok = _with_store(monkeypatch, tmp_path, ["read", "approve", "message"])
    h = {"Authorization": f"Bearer {tok}"}
    for method, path in [("POST", "/agents/new"), ("GET", "/agents/new/parents"),
                         ("POST", "/runtimes/available/refresh")]:
        resp, reached, _ = _call(method, path, h)
        assert resp.status == 403 and not reached, path
    resp, reached, _ = _call("GET", "/runtimes/available", h)
    assert reached


def test_an_admin_device_reaches_create(monkeypatch, tmp_path):
    _, _, tok = _with_store(monkeypatch, tmp_path, ["read", "admin"])
    resp, reached, _ = _call("POST", "/agents/new", {"Authorization": f"Bearer {tok}"})
    assert reached
