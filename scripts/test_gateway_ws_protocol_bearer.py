"""Device bearer in Sec-WebSocket-Protocol for /arturo/gptlive/ws (the Quest).

A browser/WebView WebSocket cannot set Authorization, so on this route ONLY the token may arrive as the
subprotocol "orchestra.bearer.<token>", offered beside "orchestra.v1". It must resolve the same principal as
the header, only "orchestra.v1" is ever echoed (never the token), never work on another route, and never be
written to any log or forwarded upstream.
Fenced exactly like test_gateway_gptlive_ws (its fixtures are reused).
"""
import asyncio
import json
import logging

import os

import aiohttp
import pytest
from aiohttp import web

import scripts.watch_gateway as G
from scripts.device_tokens import DeviceStore

HERE = os.path.dirname(os.path.abspath(__file__))
for _m, _f in ((G, "watch_gateway.py"),):
    assert os.path.realpath(_m.__file__) == os.path.join(os.path.realpath(HERE), _f), _m.__file__

FLEET = "fleet-token-for-tests"


@pytest.fixture(autouse=True)
def _fence(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ORCH_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path / "data", raising=False)
    monkeypatch.setattr(G, "gateway_token", lambda: FLEET)
    G._GPTLIVE_SESSIONS.clear()
    G._GPTLIVE_LIVE.clear()
    monkeypatch.setattr(G, "_GPTLIVE_STOPPING", [False])
    yield
    G._GPTLIVE_SESSIONS.clear()
    G._GPTLIVE_LIVE.clear()


def _mint(tmp_path, scopes, label="quest"):
    return DeviceStore(tmp_path / "data" / "state" / "devices").mint(label, scopes)


class Upstream:
    def __init__(self):
        self.headers, self.queries, self.closed = [], [], asyncio.Event()

    async def handler(self, request):
        self.headers.append(dict(request.headers))
        self.queries.append(request.query_string)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                await ws.send_str("echo:" + msg.data)
        self.closed.set()
        return ws


@pytest.fixture
async def upstream(aiohttp_server, monkeypatch):
    async def make():
        up = Upstream()
        app = web.Application()
        app.router.add_get("/native/ws", up.handler)
        srv = await aiohttp_server(app)
        monkeypatch.setattr(G, "ARTURO_GPTLIVE_WS_URL", f"ws://127.0.0.1:{srv.port}/native/ws")
        return up
    return make


@pytest.fixture
async def gw(aiohttp_client):
    return await aiohttp_client(G.build_app())

P = G.WS_BEARER_PREFIX
V1 = G.WS_SUBPROTOCOL


async def _connect(gw, protocols=(), headers=None, path="/arturo/gptlive/ws"):
    return await gw.ws_connect(path, protocols=protocols, headers=headers or {})


async def _status(gw, **kw):
    with pytest.raises(aiohttp.WSServerHandshakeError) as e:
        await _connect(gw, **kw)
    return e.value.status


async def test_protocol_token_resolves_the_same_principal_and_only_v1_is_echoed(gw, upstream, tmp_path):
    up = await upstream()
    dev, tok = _mint(tmp_path, ["ptt", "owner"])
    ws = await _connect(gw, protocols=(V1, P + tok))
    assert ws.protocol == V1                              # the selected protocol is orchestra.v1
    resp_hdr = ws._response.headers.get("Sec-WebSocket-Protocol", "")
    assert resp_hdr == V1 and tok not in resp_hdr         # the token is never reflected
    await ws.close()
    ws = await _connect(gw, protocols=(P + tok, V1))      # bearer FIRST: still only v1 is selected
    assert ws.protocol == V1
    assert tok not in ws._response.headers.get("Sec-WebSocket-Protocol", "")
    await ws.send_str("hi")
    assert (await ws.receive(timeout=2)).data == "echo:hi"
    await ws.close()
    await asyncio.wait_for(up.closed.wait(), 2)
    assert all(h.get("X-Arturo-Principal") == f"owner:{dev}" for h in up.headers)  # same owner stamp
    assert tok not in json.dumps(up.headers)                                         # never forwarded upstream


async def test_header_and_protocol_resolve_identically(gw, upstream, tmp_path):
    up = await upstream()
    dev, tok = _mint(tmp_path, ["ptt"])
    for kw in ({"headers": {"Authorization": f"Bearer {tok}"}}, {"protocols": (V1, P + tok)}):
        ws = await _connect(gw, **kw)
        await ws.close()
    await asyncio.sleep(0.1)
    assert [h.get("X-Arturo-Principal") for h in up.headers] == [f"device:{dev}"] * 2


async def test_bad_unknown_ambiguous_or_v1_less_protocol_tokens_are_401(gw, upstream, tmp_path):
    up = await upstream()
    _, tok = _mint(tmp_path, ["ptt"])
    assert await _status(gw, protocols=(V1, P + "not-a-real-token")) == 401
    assert await _status(gw, protocols=(V1, P)) == 401                          # empty token
    assert await _status(gw, protocols=(V1, P + tok, P + tok + "x")) == 401     # two bearers: fail closed
    assert await _status(gw, protocols=(V1, "orchestra.other." + tok)) == 401   # wrong prefix
    assert await _status(gw, protocols=(P + tok,)) == 401                       # orchestra.v1 not offered
    assert up.headers == []


async def test_the_fleet_bearer_never_authenticates_through_the_protocol(gw, upstream):
    up = await upstream()
    assert await _status(gw, protocols=(V1, P + FLEET)) == 401
    assert up.headers == []


async def test_a_protocol_token_without_ptt_is_403(gw, upstream, tmp_path):
    up = await upstream()
    _, tok = _mint(tmp_path, ["read", "voice"])
    assert await _status(gw, protocols=(V1, P + tok)) == 403
    assert up.headers == []


async def test_header_wins_over_protocol(gw, upstream, tmp_path):
    up = await upstream()
    _, good = _mint(tmp_path, ["ptt"])
    # a bad header is NOT rescued by a good protocol token: the header is the credential when present
    assert await _status(gw, headers={"Authorization": "Bearer nope"}, protocols=(V1, P + good)) == 401
    assert up.headers == []


async def test_header_path_is_unchanged(gw, upstream, tmp_path):
    up = await upstream()
    dev, tok = _mint(tmp_path, ["ptt"])
    ws = await _connect(gw, headers={"Authorization": f"Bearer {tok}"})
    assert ws.protocol is None                            # no subprotocol offered, none selected
    await ws.close()
    await asyncio.sleep(0.05)
    ws = await _connect(gw, headers={"Authorization": f"Bearer {FLEET}"})   # fleet bearer by HEADER still works
    await ws.close()
    await asyncio.sleep(0.05)
    assert [h.get("X-Arturo-Principal") for h in up.headers][0] == f"device:{dev}"


async def test_another_route_never_accepts_a_protocol_bearer(gw, tmp_path):
    _, tok = _mint(tmp_path, ["read", "ptt"])
    for path in ("/agents", "/events", "/pending-approvals"):
        r = await gw.get(path, headers={"Sec-WebSocket-Protocol": f"{V1}, {P}{tok}", "Upgrade": "websocket",
                                         "Connection": "Upgrade"})
        assert r.status == 401, path


async def test_the_route_needs_a_real_upgrade_for_the_protocol_path(gw, upstream, tmp_path):
    await upstream()
    _, tok = _mint(tmp_path, ["ptt"])
    r = await gw.get("/arturo/gptlive/ws", headers={"Sec-WebSocket-Protocol": f"{V1}, {P}{tok}"})
    assert r.status == 401


async def test_the_token_is_never_logged(gw, upstream, tmp_path, caplog):
    await upstream()
    caplog.set_level(logging.DEBUG)
    _, tok = _mint(tmp_path, ["ptt"])
    bad = "zzBADtokenNEVERloggedzz"
    ws = await _connect(gw, protocols=(V1, P + tok))
    await ws.close()
    assert await _status(gw, protocols=(V1, P + bad)) == 401
    # header auth + a bearer offered WITHOUT orchestra.v1: nothing could be selected, so aiohttp would print
    # the offered list. It is refused before the handshake instead (400), and nothing is logged.
    assert await _status(gw, headers={"Authorization": f"Bearer {tok}"}, protocols=(P + tok,)) == 400
    await asyncio.sleep(0.1)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    # This tree has no separate Arturo access log (gateway_access_log.py); aiohttp's own access log is what runs.
    assert "/arturo/gptlive/ws" in logged, "the access log ran (else this proves nothing)"
    assert tok not in logged and bad not in logged


async def test_head_and_trailing_slash_never_take_the_protocol_path(gw, upstream, tmp_path):
    up = await upstream()
    _, tok = _mint(tmp_path, ["ptt"])
    h = {"Sec-WebSocket-Protocol": f"{V1}, {P}{tok}", "Upgrade": "websocket", "Connection": "Upgrade"}
    r = await gw.head("/arturo/gptlive/ws", headers=h)
    assert r.status in (401, 403, 405)
    r = await gw.get("/arturo/gptlive/ws/", headers=h)
    assert r.status in (401, 404)
    assert up.headers == []


async def test_any_protocols_without_v1_are_refused_before_the_handshake(gw, upstream, tmp_path, caplog):
    """Mis-cased or otherwise unselectable protocols: refused (400) before prepare(), so aiohttp never prints
    the offered list. A header-auth client offering no protocols at all is unaffected (header path test)."""
    await upstream()
    caplog.set_level(logging.DEBUG)
    _, tok = _mint(tmp_path, ["ptt"])
    hdr = {"Authorization": f"Bearer {tok}"}
    assert await _status(gw, headers=hdr, protocols=("Orchestra.Bearer." + tok,)) == 400
    assert await _status(gw, headers=hdr, protocols=("something.else",)) == 400
    await asyncio.sleep(0.05)
    assert tok not in "\n".join(r.getMessage() for r in caplog.records)


async def test_a_token_query_param_is_never_forwarded_upstream(gw, upstream, tmp_path):
    up = await upstream()
    _, tok = _mint(tmp_path, ["ptt"])
    ws = await _connect(gw, protocols=(V1, P + tok),
                        path="/arturo/gptlive/ws?conversation_id=c1&token=LEAKME&voice=marin")
    await ws.close()
    await asyncio.sleep(0.1)
    assert up.queries == ["conversation_id=c1&voice=marin"]      # other params kept, in order
    assert "LEAKME" not in json.dumps(up.headers)


async def test_a_malformed_protocol_authenticated_upgrade_is_400_no_upstream_and_no_token_logged(gw, upstream, tmp_path,
                                                                                                caplog):
    """Review (d) on the PROTOCOL path: a bad handshake (here: a malformed key) authenticated by the bearer
    subprotocol is refused by can_prepare() BEFORE any upstream session. can_prepare runs aiohttp's handshake code,
    whose "protocols don't overlap" warning prints the offered list; it runs only after the orchestra.v1 refusal,
    on a response built with protocols=(orchestra.v1,), so the list always overlaps and the token is never logged."""
    up = await upstream()
    caplog.set_level(logging.DEBUG)
    _, tok = _mint(tmp_path, ["ptt"])
    h = {"Sec-WebSocket-Protocol": f"{V1}, {P}{tok}", "Upgrade": "websocket", "Connection": "Upgrade",
         "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": "not-a-valid-key"}
    r = await gw.get("/arturo/gptlive/ws", headers=h)
    assert r.status == 400
    assert up.headers == [], "an upstream arturo session was opened"
    await asyncio.sleep(0.05)
    assert tok not in "\n".join(r.getMessage() for r in caplog.records)
