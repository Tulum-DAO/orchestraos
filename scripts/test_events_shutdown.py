"""An open /events stream must not hold the gateway's SIGTERM drain.

Measured 2026-10-22: the phone keeps /events open; its loop had no stop condition, so aiohttp's
graceful shutdown waited on it far past restart_watch_gateway.sh's 30 s wait and the restart
stopped half way, the gateway refusing connections until it was killed. A plain aiohttp server
with one such stream was still alive 75 s after SIGTERM; with an on_shutdown stop flag, 1 s."""
import asyncio
import sys
import time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scripts.watch_gateway as G  # noqa: E402

FLEET = "fleet-bearer-0123456789abcdef-for-tests"


class _NoStore:                        # never the default ApprovalStore(): its path is the live one
    def migrate(self):
        pass


@pytest.fixture
def box(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    tok = tmp_path / "gateway-token"
    tok.write_text(FLEET)
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    monkeypatch.setattr(G, "ORCH_DIR", data)
    monkeypatch.setattr(G, "TOKEN_FILE", tok)
    monkeypatch.setattr(G, "ApprovalStore", _NoStore)
    monkeypatch.setattr(G, "_events_digest", lambda store: "a1|")
    monkeypatch.setattr(G, "EVENTS_TICK_S", 0.05)


def test_an_open_events_stream_ends_when_the_gateway_shuts_down(box):
    async def go():
        server = TestServer(G.build_app())
        client = TestClient(server)
        await client.start_server()
        r = await client.get("/events", headers={"Authorization": f"Bearer {FLEET}"})
        assert r.status == 200
        assert (await r.content.readline()).startswith(b"data: ")     # the stream is live
        t0 = time.monotonic()
        await asyncio.wait_for(server.close(), timeout=10)           # runs on_shutdown, then drains
        r.close()
        await client.close()
        return time.monotonic() - t0
    assert asyncio.run(go()) < 10


def test_the_events_stop_hook_is_registered_on_the_app():
    assert G._events_on_shutdown in list(G.build_app().on_shutdown)


def _open(client):
    return client.get("/events", headers={"Authorization": f"Bearer {FLEET}"})


def test_a_digest_still_running_does_not_hold_the_drain(box, monkeypatch):
    """Codex r1 on #465: the loop cannot see a flag while it awaits the digest in the executor."""
    import threading
    release, calls = threading.Event(), []

    def slow_digest(store):
        calls.append(1)
        if len(calls) > 1:                                     # the first answers, the next one hangs
            release.wait(30)
        return f"a{len(calls)}|"
    monkeypatch.setattr(G, "_events_digest", slow_digest)

    async def go():
        server = TestServer(G.build_app())
        client = TestClient(server)
        await client.start_server()
        r = await _open(client)
        assert (await r.content.readline()).startswith(b"data: ")
        while len(calls) < 2:                                  # now blocked inside the digest
            await asyncio.sleep(0.02)
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(server.close(), timeout=10)
            return time.monotonic() - t0
        finally:
            release.set()
            r.close()
            await client.close()
    assert asyncio.run(go()) < 10


def test_shutting_down_one_app_leaves_another_apps_stream_running(box):
    """Codex r1 on #465: the stop state is the app's own, never the process's."""
    async def go():
        a, b = TestServer(G.build_app()), TestServer(G.build_app())
        ca, cb = TestClient(a), TestClient(b)
        await ca.start_server()
        await cb.start_server()
        rb = await _open(cb)
        assert (await rb.content.readline()).startswith(b"data: ")
        await asyncio.wait_for(a.close(), timeout=10)
        await ca.close()
        rb2 = await _open(cb)                                  # a stream opened after A stopped
        line = await asyncio.wait_for(rb2.content.readline(), timeout=5)
        await asyncio.sleep(0.3)                               # several ticks: still open
        alive = not rb2.content.at_eof()
        rb.close()
        rb2.close()
        await cb.close()
        return line, alive
    line, alive = asyncio.run(go())
    assert line.startswith(b"data: ") and alive
