"""GET /terminal/ws — the gateway's WebSocket relay to the API's live terminal (/ws/terminal), so a
native app (the Mac's Dev mode) gets the web's xterm experience through the one base and bearer it
already holds. The API's socket has only a browser-origin check and is loopback-only; this route is
the audited, scoped way in.

It is a shell into a seat: unlimited keystrokes, ^C, ^D, a `!` escape. So: a paired DEVICE token
holding `admin` AND `inject` (the fleet bearer is refused even though '*' passes `admin`), a session
that GET /agents lists, no Funnel and no off-tailnet peer, one socket per (device, session), a
gateway-wide cap, an idle close, and an operator-actions ledger row at open and at close that never
carries a keystroke.

Fenced: tmp ORCHESTRA_DIR (device store + ledger), fleet token patched, the upstream is a fake
aiohttp WebSocket server on a random loopback port. No network, no tmux.
"""
import asyncio
import json
import os
import sys

import aiohttp
import pytest
from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import scripts.watch_gateway as G  # noqa: E402
from scripts.device_tokens import DeviceStore  # noqa: E402

FLEET = "fleet-token-for-tests"
ROUTE = "/terminal/ws"


@pytest.fixture(autouse=True)
def _fence(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ORCH_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path / "data")
    monkeypatch.setattr(G, "gateway_token", lambda: FLEET)
    monkeypatch.setattr(G, "compute_agents", lambda: [{"id": "a", "tmux_session": "seat-a"},
                                                      {"id": "b", "tmux_session": "seat-b"},
                                                      {"id": "c"}])                 # no pane: never a target
    monkeypatch.setattr(G, "_protected_refusal", lambda request, session: None)
    G._TERMINAL_SESSIONS.clear()
    G._TERMINAL_LIVE.clear()
    yield
    G._TERMINAL_SESSIONS.clear()
    G._TERMINAL_LIVE.clear()


def _mint(tmp_path, scopes, label="mac"):
    return DeviceStore(tmp_path / "data" / "state" / "devices").mint(label, scopes)


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


def _ledger(tmp_path):
    p = tmp_path / "data" / "state" / "operator-actions.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


class Upstream:
    """A fake API /ws/terminal: records the upgrade headers + query, echoes frames, and can be told
    to refuse with a status or to close with a code."""

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
    async def make(**kw):
        up = Upstream(**kw)
        app = web.Application()
        app.router.add_get("/ws/terminal", up.handler)
        srv = await aiohttp_server(app)
        monkeypatch.setattr(G, "API_URL", f"http://127.0.0.1:{srv.port}")
        return up
    return make


@pytest.fixture
async def gw(aiohttp_client):
    return await aiohttp_client(G.build_app())


async def _refused(gw, headers=None, path=ROUTE + "?session=seat-a&cols=80&rows=24"):
    with pytest.raises(aiohttp.WSServerHandshakeError) as e:
        await gw.ws_connect(path, headers=headers or {})
    return e.value.status


async def _open(gw, tmp_path, scopes=("admin", "inject"), session="seat-a", label="mac", **kw):
    dev_id, tok = _mint(tmp_path, list(scopes), label=label)
    headers = {**_bearer(tok), **kw.pop("headers", {})}
    ws = await gw.ws_connect(f"{ROUTE}?session={session}&cols=80&rows=24", headers=headers, **kw)
    return ws, dev_id


# --------------------------------------------------------------------- refused before the upgrade

def test_route_is_declared_admin_scope():
    assert G.ROUTE_SCOPES[("GET", ROUTE)] == "admin"


async def test_no_bearer_is_401_and_never_reaches_the_api(gw, upstream):
    up = await upstream()
    assert await _refused(gw) == 401
    assert up.headers == []


async def test_the_fleet_bearer_is_403_even_though_star_passes_admin(gw, upstream):
    """The audit line needs a device id; the fleet bearer identifies no device."""
    up = await upstream()
    assert await _refused(gw, _bearer(FLEET)) == 403
    assert up.headers == []


async def test_a_device_without_admin_is_403(gw, upstream, tmp_path):
    await upstream()
    _, tok = _mint(tmp_path, ["read", "inject"])
    assert await _refused(gw, _bearer(tok)) == 403


async def test_a_device_with_admin_but_without_inject_is_403(gw, upstream, tmp_path):
    """It presses keys, so it needs the key-pressing verb too (gm: inject + admin)."""
    up = await upstream()
    _, tok = _mint(tmp_path, ["admin"])
    assert await _refused(gw, _bearer(tok)) == 403
    assert up.headers == []


async def test_a_token_in_the_query_string_is_not_accepted(gw, upstream, tmp_path):
    _, tok = _mint(tmp_path, ["admin", "inject"])
    assert await _refused(gw, path=f"{ROUTE}?session=seat-a&token={tok}") == 401


@pytest.mark.parametrize("session", ["", "../etc", "seat a", "seat;rm", "x" * 81])
async def test_a_bad_session_name_is_400(gw, upstream, tmp_path, session):
    up = await upstream()
    _, tok = _mint(tmp_path, ["admin", "inject"])
    assert await _refused(gw, _bearer(tok), path=f"{ROUTE}?session={session}") == 400
    assert up.headers == []


async def test_a_session_that_agents_does_not_list_is_404(gw, upstream, tmp_path):
    """Never an arbitrary tmux name: only a seat GET /agents lists."""
    up = await upstream()
    _, tok = _mint(tmp_path, ["admin", "inject"])
    assert await _refused(gw, _bearer(tok), path=f"{ROUTE}?session=not-a-seat") == 404
    assert up.headers == []


def test_known_sessions_are_the_tmux_sessions_agents_lists(monkeypatch):
    """The same rows GET /agents serves (compute_agents); a row without a pane is never a target, and
    a listing that fails names nothing (fail closed)."""
    assert G._terminal_known_sessions() == {"seat-a", "seat-b"}
    monkeypatch.setattr(G, "compute_agents", lambda: (_ for _ in ()).throw(RuntimeError("tmux gone")))
    assert G._terminal_known_sessions() == set()


async def test_a_funnel_request_is_404(gw, upstream, tmp_path):
    """Tailnet-only: a request that came through Tailscale Funnel never gets a shell."""
    up = await upstream()
    _, tok = _mint(tmp_path, ["admin", "inject"])
    assert await _refused(gw, {**_bearer(tok), "Tailscale-Funnel-Request": "1"}) == 404
    assert up.headers == []


async def test_a_peer_off_the_tailnet_is_404(gw, upstream, tmp_path, monkeypatch):
    up = await upstream()
    monkeypatch.setattr(G, "_agent_file_peer", lambda request: "203.0.113.5")
    _, tok = _mint(tmp_path, ["admin", "inject"])
    assert await _refused(gw, _bearer(tok)) == 404
    assert up.headers == []


async def test_a_protected_session_refusal_is_403(gw, upstream, tmp_path, monkeypatch):
    up = await upstream()
    monkeypatch.setattr(G, "_protected_refusal",
                        lambda request, session: {"ok": False, "error": "protected session"})
    _, tok = _mint(tmp_path, ["admin", "inject"])
    assert await _refused(gw, _bearer(tok)) == 403
    assert up.headers == []


async def test_a_plain_GET_without_upgrade_headers_is_400_before_any_upstream_session(gw, upstream, tmp_path):
    up = await upstream()
    _, tok = _mint(tmp_path, ["admin", "inject"])
    r = await gw.get(f"{ROUTE}?session=seat-a", headers=_bearer(tok))
    assert r.status == 400
    assert up.headers == []


# --------------------------------------------------------------------- the relay

async def test_frames_pass_verbatim_both_ways_text_and_binary(gw, upstream, tmp_path):
    up = await upstream()
    ws, _ = await _open(gw, tmp_path)
    await ws.send_str("ls -la\r")
    assert (await ws.receive()).data == "echo:ls -la\r"
    await ws.send_str(json.dumps({"type": "resize", "cols": 132, "rows": 40}))   # just a text frame here
    assert (await ws.receive()).data == 'echo:{"type": "resize", "cols": 132, "rows": 40}'
    await ws.send_bytes(b"\x1b[A")
    assert (await ws.receive()).data == b"echo:\x1b[A"
    assert up.got == [(aiohttp.WSMsgType.TEXT, "ls -la\r"),
                      (aiohttp.WSMsgType.TEXT, '{"type": "resize", "cols": 132, "rows": 40}'),
                      (aiohttp.WSMsgType.BINARY, b"\x1b[A")]
    await ws.close()


async def test_upstream_gets_session_cols_rows_and_loopback_origin_but_no_bearer(gw, upstream, tmp_path):
    """Only the three terminal params reach the API (never a token-shaped one), and the upgrade
    carries the API's required loopback Origin but never the device's bearer."""
    up = await upstream()
    _, tok = _mint(tmp_path, ["admin", "inject"])
    ws = await gw.ws_connect(f"{ROUTE}?session=seat-a&cols=100&rows=30&token=leak&machine=m",
                             headers=_bearer(tok))
    await ws.close()
    assert up.query == [{"session": "seat-a", "cols": "100", "rows": "30"}]
    h = {k.lower(): v for k, v in up.headers[0].items()}
    assert h.get("origin") == "http://127.0.0.1:8888"
    assert "authorization" not in h


async def test_cols_and_rows_are_optional(gw, upstream, tmp_path):
    up = await upstream()
    _, tok = _mint(tmp_path, ["admin", "inject"])
    ws = await gw.ws_connect(f"{ROUTE}?session=seat-a", headers=_bearer(tok))
    await ws.close()
    assert up.query == [{"session": "seat-a"}]


@pytest.mark.parametrize("status", [403, 404, 503])
async def test_an_upstream_refusal_status_is_forwarded_before_the_upgrade(gw, upstream, tmp_path, status):
    await upstream(refuse=status)
    _, tok = _mint(tmp_path, ["admin", "inject"])
    assert await _refused(gw, _bearer(tok)) == status


async def test_upstream_unreachable_is_502(gw, tmp_path, monkeypatch):
    monkeypatch.setattr(G, "API_URL", "http://127.0.0.1:9")
    _, tok = _mint(tmp_path, ["admin", "inject"])
    assert await _refused(gw, _bearer(tok)) == 502


async def test_device_close_reaches_upstream(gw, upstream, tmp_path):
    up = await upstream()
    ws, _ = await _open(gw, tmp_path)
    await ws.close(code=1000, message=b"bye")
    await asyncio.wait_for(up.closed.wait(), 3)
    assert up.close_code == 1000


async def test_upstream_close_code_and_reason_reach_the_device(gw, upstream, tmp_path):
    await upstream(close_with=(1011, b"pty exited"))
    ws, _ = await _open(gw, tmp_path)
    msg = await ws.receive()
    assert msg.type == aiohttp.WSMsgType.CLOSE and msg.data == 1011 and msg.extra == "pty exited"


# --------------------------------------------------------------------- one per (device, session), a cap, idle

async def test_a_second_socket_for_the_same_device_and_session_is_409_and_another_session_is_fine(gw, upstream, tmp_path):
    await upstream()
    dev_id, tok = _mint(tmp_path, ["admin", "inject"])
    ws = await gw.ws_connect(f"{ROUTE}?session=seat-a", headers=_bearer(tok))
    assert await _refused(gw, _bearer(tok), path=f"{ROUTE}?session=seat-a") == 409
    ws_b = await gw.ws_connect(f"{ROUTE}?session=seat-b", headers=_bearer(tok))    # a second agent's tab
    await ws_b.close()
    await ws.close()
    await asyncio.sleep(0.05)
    ws2 = await gw.ws_connect(f"{ROUTE}?session=seat-a", headers=_bearer(tok))     # slot freed on close
    await ws2.close()


async def test_two_devices_may_watch_the_same_seat(gw, upstream, tmp_path):
    await upstream()
    ws1, _ = await _open(gw, tmp_path, label="mac")
    ws2, _ = await _open(gw, tmp_path, label="other-mac")
    await ws1.close()
    await ws2.close()


async def test_the_gateway_wide_cap_is_429(gw, upstream, tmp_path, monkeypatch):
    await upstream()
    monkeypatch.setattr(G, "TERMINAL_MAX_SESSIONS", 2)
    socks = []
    for i in range(2):
        ws, _ = await _open(gw, tmp_path, label=f"mac{i}")
        socks.append(ws)
    _, tok = _mint(tmp_path, ["admin", "inject"], label="mac9")
    assert await _refused(gw, _bearer(tok)) == 429
    for ws in socks:
        await ws.close()


async def test_an_idle_socket_is_closed_1000_idle_both_ways(gw, upstream, tmp_path, monkeypatch):
    up = await upstream()
    monkeypatch.setattr(G, "TERMINAL_IDLE_S", 0.3)
    ws, _ = await _open(gw, tmp_path)
    msg = await asyncio.wait_for(ws.receive(), 3)
    assert msg.type == aiohttp.WSMsgType.CLOSE and msg.data == 1000 and msg.extra == "idle"
    await asyncio.wait_for(up.closed.wait(), 3)


async def test_traffic_keeps_a_socket_alive_past_the_idle_window(gw, upstream, tmp_path, monkeypatch):
    await upstream()
    monkeypatch.setattr(G, "TERMINAL_IDLE_S", 0.4)
    ws, _ = await _open(gw, tmp_path)
    for _ in range(3):
        await asyncio.sleep(0.25)
        await ws.send_str("k")
        assert (await asyncio.wait_for(ws.receive(), 3)).data == "echo:k"
    await ws.close()


# --------------------------------------------------------------------- the audit line

async def test_open_and_close_are_in_the_operator_actions_ledger_without_a_keystroke(gw, upstream, tmp_path):
    await upstream()
    ws, dev_id = await _open(gw, tmp_path, label="mac", headers={"X-Surface": "mac"})
    await ws.send_str("echo SECRET-KEYSTROKE\r")
    await ws.receive()
    await ws.close(code=1000, message=b"done")
    for _ in range(40):
        rows = [r for r in _ledger(tmp_path) if r.get("kind") == "terminal"]
        if len(rows) == 2:
            break
        await asyncio.sleep(0.05)
    assert [r["event"] for r in rows] == ["open", "close"]
    for r in rows:
        assert r["target"] == "seat-a" and r["device"] == f"{dev_id}:mac" and r["surface"] == "mac"
        assert r["person"] is True and "at" in r
    assert rows[1]["code"] == 1000
    raw = (tmp_path / "data" / "state" / "operator-actions.jsonl").read_text()
    assert "SECRET-KEYSTROKE" not in raw and "said" not in raw


async def test_a_refused_upgrade_writes_no_ledger_row(gw, upstream, tmp_path):
    await upstream(refuse=503)
    _, tok = _mint(tmp_path, ["admin", "inject"])
    await _refused(gw, _bearer(tok))
    assert [r for r in _ledger(tmp_path) if r.get("kind") == "terminal"] == []


async def test_shutdown_closes_live_terminals_1001(gw, upstream, tmp_path):
    await upstream()
    ws, _ = await _open(gw, tmp_path)
    await G._terminal_close_all(1001, "shutdown")
    msg = await asyncio.wait_for(ws.receive(), 3)
    assert msg.type == aiohttp.WSMsgType.CLOSE and msg.data == 1001
