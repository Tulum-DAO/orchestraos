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
    monkeypatch.setattr(G, "_EVENTS_STOPPING", [False], raising=False)


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
