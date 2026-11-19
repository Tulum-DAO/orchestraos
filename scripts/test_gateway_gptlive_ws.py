"""GET /arturo/gptlive/ws — the gateway's WebSocket proxy for native GPT-Live.

The iOS app opens ONE WebSocket here with its device bearer; the gateway refuses anyone else
BEFORE the upgrade, connects to arturo-proxy's loopback native endpoint with a principal it
builds itself (never the client's), and passes frames through verbatim both ways.

Fenced: tmp ORCHESTRA_DIR (device store + stamp secret), tmp access log, fleet token patched,
the upstream is a fake aiohttp WebSocket server on a random loopback port. No network.
"""
import asyncio
import json
import os

import aiohttp
import pytest
from aiohttp import web

import scripts.watch_gateway as G                # scripts/ on sys.path, and build_app's lazy import would
from scripts.device_tokens import DeviceStore    # then grade the LIVE access log from a worktree

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
    for _reset in ("_GPTLIVE_SESSIONS", "_GPTLIVE_LIVE"):
        getattr(G, _reset, set()).clear()
    monkeypatch.setattr(G, "_GPTLIVE_STOPPING", [False], raising=False)
    yield
    for _reset in ("_GPTLIVE_SESSIONS", "_GPTLIVE_LIVE"):
        getattr(G, _reset, set()).clear()


def _mint(tmp_path, scopes, label="iphone"):
    return DeviceStore(tmp_path / "data" / "state" / "devices").mint(label, scopes)


class Upstream:
    """A fake arturo-proxy native endpoint: records the upgrade headers, echoes frames, and can
    be told to refuse, close with a code, or say something first."""

    def __init__(self, refuse=None, close_with=None):
        self.refuse, self.close_with = refuse, close_with
        self.headers, self.query, self.got, self.closed = [], [], [], asyncio.Event()
        self.close_code = None

    async def handler(self, request):
        self.headers.append(dict(request.headers))
        self.query.append(dict(request.query))
        if self.refuse:
            return web.Response(status=self.refuse, text="nope")
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if self.close_with:
            await ws.close(code=self.close_with[0], message=self.close_with[1])
            self.closed.set()
            return ws
        async for msg in ws:
            self.got.append((msg.type, msg.data))
            if msg.type == aiohttp.WSMsgType.TEXT:
                await ws.send_str("echo:" + msg.data)
            elif msg.type == aiohttp.WSMsgType.BINARY:
                await ws.send_bytes(b"echo:" + msg.data)
        self.close_code = ws.close_code
        self.closed.set()
        return ws


@pytest.fixture
async def upstream(aiohttp_server, monkeypatch):
    holder = {}

    async def make(**kw):
        up = Upstream(**kw)
        app = web.Application()
        app.router.add_get("/native/ws", up.handler)
        srv = await aiohttp_server(app)
        monkeypatch.setattr(G, "ARTURO_GPTLIVE_WS_URL", f"ws://127.0.0.1:{srv.port}/native/ws")
        holder["up"] = up
        return up
    return make


@pytest.fixture
async def gw(aiohttp_client):
    return await aiohttp_client(G.build_app())


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


async def _refused(gw, headers=None, path="/arturo/gptlive/ws"):
    with pytest.raises(aiohttp.WSServerHandshakeError) as e:
        await gw.ws_connect(path, headers=headers or {})
    return e.value.status


# --------------------------------------------------------------------- refused before upgrade

async def test_route_is_declared_ptt_scope():
    assert G.ROUTE_SCOPES[("GET", "/arturo/gptlive/ws")] == "ptt"


async def test_no_bearer_is_refused_401_and_never_reaches_arturo(gw, upstream):
    up = await upstream()
    assert await _refused(gw) == 401
    assert up.headers == []


async def test_an_unknown_token_is_refused_401(gw, upstream):
    up = await upstream()
    assert await _refused(gw, _bearer("not-a-real-token")) == 401
    assert up.headers == []


async def test_a_token_in_the_query_string_is_not_accepted(gw, upstream, tmp_path):
    up = await upstream()
    _, tok = _mint(tmp_path, ["ptt"])
    assert await _refused(gw, path=f"/arturo/gptlive/ws?token={tok}") == 401
    assert up.headers == []


async def test_a_paired_device_without_ptt_is_refused_403(gw, upstream, tmp_path):
    """The foreign-device case: a real, paired token that was never granted the voice brain."""
    up = await upstream()
    _, tok = _mint(tmp_path, ["read", "voice", "message"])
    assert await _refused(gw, _bearer(tok)) == 403
    assert up.headers == []


async def test_a_revoked_device_is_refused_401(gw, upstream, tmp_path):
    up = await upstream()
    dev, tok = _mint(tmp_path, ["ptt"])
    DeviceStore(tmp_path / "data" / "state" / "devices").revoke(dev)
    assert await _refused(gw, _bearer(tok)) == 401
    assert up.headers == []


# --------------------------------------------------------------------- the session itself

async def test_frames_pass_verbatim_both_ways_text_and_binary(gw, upstream, tmp_path):
    up = await upstream()
    _, tok = _mint(tmp_path, ["ptt"])
    ws = await gw.ws_connect("/arturo/gptlive/ws?conversation_id=c1&voice=marin", headers=_bearer(tok))
    await ws.send_str('{"type":"hello"}')
    assert (await ws.receive(timeout=2)).data == 'echo:{"type":"hello"}'
    await ws.send_bytes(b"\x00\x01pcm")
    assert (await ws.receive(timeout=2)).data == b"echo:\x00\x01pcm"
    await ws.close()
    await asyncio.wait_for(up.closed.wait(), 2)
    assert up.got[:2] == [(aiohttp.WSMsgType.TEXT, '{"type":"hello"}'), (aiohttp.WSMsgType.BINARY, b"\x00\x01pcm")]
    assert up.query[0] == {"conversation_id": "c1", "voice": "marin"}


async def test_upstream_sees_the_gateway_principal_never_the_clients(gw, upstream, tmp_path):
    up = await upstream()
    dev, tok = _mint(tmp_path, ["ptt"])
    forged = {"X-Arturo-Principal": "fleet", "X-Arturo-Stamp": "forged", "X-Arturo-Stamp-Secret": "forged"}  # pragma: allowlist secret (a forged value the test proves is ignored)
    ws = await gw.ws_connect("/arturo/gptlive/ws", headers={**_bearer(tok), **forged})
    await ws.close()
    await asyncio.wait_for(up.closed.wait(), 2)
    h = up.headers[0]
    assert h.get("X-Arturo-Principal") == f"device:{dev}"
    assert "forged" not in json.dumps(h)
    assert tok not in json.dumps(h)                       # the device bearer never goes upstream


async def test_an_owner_device_is_stamped_owner_with_the_secret(gw, upstream, tmp_path):
    up = await upstream()
    dev, tok = _mint(tmp_path, ["ptt", "owner"])
    ws = await gw.ws_connect("/arturo/gptlive/ws", headers=_bearer(tok))
    await ws.close()
    await asyncio.wait_for(up.closed.wait(), 2)
    from scripts import arturo_stamp
    assert up.headers[0].get("X-Arturo-Principal") == f"owner:{dev}"
    assert up.headers[0].get(arturo_stamp.HEADER)


async def test_device_close_reaches_upstream(gw, upstream, tmp_path):
    up = await upstream()
    _, tok = _mint(tmp_path, ["ptt"])
    ws = await gw.ws_connect("/arturo/gptlive/ws", headers=_bearer(tok))
    await ws.close(code=1000)
    await asyncio.wait_for(up.closed.wait(), 2)
    assert up.close_code == 1000


async def test_upstream_close_code_and_reason_reach_the_device(gw, upstream, tmp_path):
    await upstream(close_with=(4002, b"session cap reached"))
    _, tok = _mint(tmp_path, ["ptt"])
    ws = await gw.ws_connect("/arturo/gptlive/ws", headers=_bearer(tok))
    msg = await ws.receive(timeout=2)
    assert msg.type == aiohttp.WSMsgType.CLOSE and msg.data == 4002 and msg.extra == "session cap reached"


async def test_flag_off_upstream_404_is_an_honest_404_before_upgrade(gw, upstream, tmp_path):
    await upstream(refuse=404)
    _, tok = _mint(tmp_path, ["ptt"])
    assert await _refused(gw, _bearer(tok)) == 404


@pytest.mark.parametrize("status", [429, 403, 503, 500])
async def test_any_upstream_error_status_before_the_upgrade_is_forwarded_as_is(gw, upstream, tmp_path, status):
    """flag OFF -> 503 is THE 'native is off' signal; every 4xx/5xx passes."""
    await upstream(refuse=status)
    _, tok = _mint(tmp_path, ["ptt"])
    assert await _refused(gw, _bearer(tok)) == status


async def test_a_non_error_non_upgrade_upstream_answer_is_502(gw, upstream, tmp_path):
    await upstream(refuse=200)
    _, tok = _mint(tmp_path, ["ptt"])
    assert await _refused(gw, _bearer(tok)) == 502


async def test_default_upstream_is_the_native_service():
    assert G.ARTURO_GPTLIVE_WS_URL == os.environ.get("ARTURO_GPTLIVE_WS_URL", "ws://127.0.0.1:5076/native/ws")


async def test_upstream_gets_X_Arturo_Device_built_by_the_gateway(gw, upstream, tmp_path):
    up = await upstream()
    dev, tok = _mint(tmp_path, ["ptt", "owner"])
    ws = await gw.ws_connect("/arturo/gptlive/ws", headers={**_bearer(tok), "X-Arturo-Device": "forged-dev"})
    await ws.close()
    await asyncio.wait_for(up.closed.wait(), 2)
    assert up.headers[0].get("X-Arturo-Device") == dev
    assert "forged-dev" not in json.dumps(up.headers[0])


async def test_upstream_unreachable_is_502(gw, monkeypatch, tmp_path):
    monkeypatch.setattr(G, "ARTURO_GPTLIVE_WS_URL", "ws://127.0.0.1:9/gptlive/ws")
    _, tok = _mint(tmp_path, ["ptt"])
    assert await _refused(gw, _bearer(tok)) == 502
    assert G._GPTLIVE_SESSIONS == {}                     # a failed connect frees its slot


# --------------------------------------------------------------------- limits + idle

async def test_one_session_per_device_second_is_409(gw, upstream, tmp_path):
    await upstream()
    _, tok = _mint(tmp_path, ["ptt"])
    ws = await gw.ws_connect("/arturo/gptlive/ws", headers=_bearer(tok))
    assert await _refused(gw, _bearer(tok)) == 409
    await ws.close()
    for _ in range(40):
        if not G._GPTLIVE_SESSIONS:
            break
        await asyncio.sleep(0.05)
    ws2 = await gw.ws_connect("/arturo/gptlive/ws", headers=_bearer(tok))   # slot freed on close
    await ws2.close()


async def test_gateway_wide_cap_is_429(gw, upstream, tmp_path, monkeypatch):
    await upstream()
    monkeypatch.setattr(G, "GPTLIVE_MAX_SESSIONS", 2)
    socks = []
    for i in range(2):
        _, tok = _mint(tmp_path, ["ptt"], label=f"d{i}")
        socks.append(await gw.ws_connect("/arturo/gptlive/ws", headers=_bearer(tok)))
    _, tok3 = _mint(tmp_path, ["ptt"], label="d3")
    assert await _refused(gw, _bearer(tok3)) == 429
    for s in socks:
        await s.close()


async def test_idle_session_is_closed_both_ways(gw, upstream, tmp_path, monkeypatch):
    up = await upstream()
    monkeypatch.setattr(G, "GPTLIVE_IDLE_S", 0.3)
    _, tok = _mint(tmp_path, ["ptt"])
    ws = await gw.ws_connect("/arturo/gptlive/ws", headers=_bearer(tok))
    msg = await ws.receive(timeout=3)
    assert msg.type == aiohttp.WSMsgType.CLOSE and msg.data == 1000 and msg.extra == "idle"
    await asyncio.wait_for(up.closed.wait(), 2)


async def test_traffic_keeps_a_session_alive_past_the_idle_window(gw, upstream, tmp_path, monkeypatch):
    await upstream()
    monkeypatch.setattr(G, "GPTLIVE_IDLE_S", 0.4)
    _, tok = _mint(tmp_path, ["ptt"])
    ws = await gw.ws_connect("/arturo/gptlive/ws", headers=_bearer(tok))
    for i in range(6):                                    # 6 x 0.15 s = 0.9 s > 2 idle windows
        await ws.send_str(f"t{i}")
        assert (await ws.receive(timeout=2)).data == f"echo:t{i}"
        await asyncio.sleep(0.15)
    await ws.close()


# test_one_access_line_per_session_no_token: not ported. This tree has no Arturo access log
# (gateway_access_log.py); the token-never-logged check lives in test_gateway_ws_protocol_bearer.


async def _prod_gateway():
    """PRODUCTION MODE: watch_gateway runs web.run_app (handler_cancellation=False); aiohttp's TestServer forces
    True, which cancels the handler when the client leaves. So these run their own AppRunner, as run_app does."""
    from aiohttp import web as _web
    runner = _web.AppRunner(G.build_app(), handler_cancellation=False)
    await runner.setup()
    site = _web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, site._server.sockets[0].getsockname()[1]


async def test_a_device_that_vanishes_closes_arturo_1001(upstream, tmp_path):
    """The phone loses signal: no close frame, the TCP connection just dies."""
    up = await upstream()
    _, tok = _mint(tmp_path, ["ptt"])
    runner, port = await _prod_gateway()
    try:
        async with aiohttp.ClientSession() as s:
            ws = await s.ws_connect(f"http://127.0.0.1:{port}/arturo/gptlive/ws", headers=_bearer(tok))
            await ws.send_str("x")
            await ws.receive(timeout=2)
            ws._response.connection.transport.abort()      # vanish: no CLOSE frame
            await asyncio.wait_for(up.closed.wait(), 3)
    finally:
        await runner.cleanup()
    assert up.close_code == 1001
    assert G._GPTLIVE_SESSIONS == {}


async def test_arturo_dropping_abnormally_reaches_the_device_as_1011(aiohttp_server, gw, monkeypatch, tmp_path):
    async def drop(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive()                                  # wait for one frame, then die uncleanly
        request.transport.abort()
        return ws
    app = web.Application()
    app.router.add_get("/native/ws", drop)
    srv = await aiohttp_server(app)
    monkeypatch.setattr(G, "ARTURO_GPTLIVE_WS_URL", f"ws://127.0.0.1:{srv.port}/native/ws")
    _, tok = _mint(tmp_path, ["ptt"])
    ws = await gw.ws_connect("/arturo/gptlive/ws", headers=_bearer(tok))
    await ws.send_str("x")
    msg = await ws.receive(timeout=3)
    assert msg.type == aiohttp.WSMsgType.CLOSE and msg.data == 1011


async def test_gateway_shutdown_closes_open_sessions_1001_promptly(upstream, tmp_path):
    """an OPEN native call held aiohttp shutdown, so
    restart_watch_gateway.sh's 30 s wait could expire with NOT RESTARTED. on_shutdown closes every
    live relay with 1001 'gateway restart' to the device and to arturo, before connections drain."""
    import time
    up = await upstream()
    _, tok = _mint(tmp_path, ["ptt"])
    runner, port = await _prod_gateway()
    async with aiohttp.ClientSession() as s:
        ws = await s.ws_connect(f"http://127.0.0.1:{port}/arturo/gptlive/ws", headers=_bearer(tok))
        await ws.send_str("x")
        await ws.receive(timeout=2)
        t0 = time.monotonic()
        cleanup = asyncio.ensure_future(runner.cleanup())
        msg = await ws.receive(timeout=5)
        await asyncio.wait_for(cleanup, 10)
        took = time.monotonic() - t0
    assert msg.type == aiohttp.WSMsgType.CLOSE and msg.data == 1001 and msg.extra == "gateway restart"
    await asyncio.wait_for(up.closed.wait(), 3)
    assert up.close_code == 1001
    assert took < 5, f"shutdown took {took:.1f}s with a session open"
    assert G._GPTLIVE_SESSIONS == {} and not G._GPTLIVE_LIVE


# ------------------------------------------- review (d): validate the handshake BEFORE any upstream session
# A malformed but authenticated upgrade opened an arturo-native session first (then ws.prepare refused it).
# The handshake is now checked with can_prepare() before connecting upstream; arturo's own refusals still
# reach the device as an HTTP status before the upgrade (so prepare() itself is not moved first).

_GOOD_UPGRADE = {"Connection": "Upgrade", "Upgrade": "websocket", "Sec-WebSocket-Version": "13",
                 "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}


@pytest.mark.parametrize("bad", [
    {"Sec-WebSocket-Version": "99"},                   # unsupported version (aiohttp accepts 13, 8, 7)
    {"Sec-WebSocket-Key": ""},                         # no key
    {"Sec-WebSocket-Key": "not-base64-16-bytes"},      # malformed key
    {"Upgrade": "h2c"},                                # not a websocket upgrade
])
async def test_a_malformed_authenticated_upgrade_never_opens_an_upstream_session(gw, upstream, tmp_path, bad):
    up = await upstream()
    _, tok = _mint(tmp_path, ["ptt"])
    hdrs = {**_bearer(tok), **_GOOD_UPGRADE, **bad}
    hdrs = {k: v for k, v in hdrs.items() if v != ""}
    r = await gw.get("/arturo/gptlive/ws", headers=hdrs)
    assert r.status == 400
    assert up.headers == [], "an upstream arturo session was opened for a handshake that could never succeed"
    assert not G._GPTLIVE_SESSIONS, "the device slot was not released"


async def test_a_good_upgrade_still_connects_after_the_early_check(gw, upstream, tmp_path):
    up = await upstream()
    _, tok = _mint(tmp_path, ["ptt"])
    ws = await gw.ws_connect("/arturo/gptlive/ws", headers=_bearer(tok))
    await ws.send_str("hi")
    assert (await ws.receive()).data == "echo:hi" and len(up.headers) == 1
    await ws.close()


async def test_arturos_refusal_still_reaches_the_device_as_http_status(gw, upstream, tmp_path):
    await upstream(refuse=503)
    _, tok = _mint(tmp_path, ["ptt"])
    assert await _refused(gw, _bearer(tok)) == 503
