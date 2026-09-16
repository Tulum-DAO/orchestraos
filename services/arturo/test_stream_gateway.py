"""RED-first tests — watch-gateway thin-proxy legs for the v2/(b) stream relay.

Auth + forward ONLY (same trust model as /arturo/ptt): Bearer at the gateway, loopback
:5071 upstream, zero relay/brain logic in the gateway.
"""
import importlib.util
import pathlib

import pytest
from aiohttp.test_utils import make_mocked_request


def _load_gateway():
    spec = importlib.util.spec_from_file_location(
        "watch_gateway_stream", pathlib.Path("scripts/watch_gateway.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_stream_audio_requires_bearer():
    mod = _load_gateway()
    req = make_mocked_request("POST", "/arturo/ptt/stream/audio")
    resp = await mod.handle_arturo_ptt_stream_audio(req)
    assert resp.status == 401


async def test_stream_events_requires_bearer():
    mod = _load_gateway()
    req = make_mocked_request("GET", "/arturo/ptt/stream/events")
    resp = await mod.handle_arturo_ptt_stream_events(req)
    assert resp.status == 401


async def test_stream_end_requires_bearer():
    mod = _load_gateway()
    req = make_mocked_request("POST", "/arturo/ptt/stream/end")
    resp = await mod.handle_arturo_ptt_stream_end(req)
    assert resp.status == 401


def test_stream_routes_registered():
    mod = _load_gateway()
    app = mod.build_app()
    routes = {(r.method, r.resource.canonical) for r in app.router.routes()}
    assert ("POST", "/arturo/ptt/stream/audio") in routes
    assert ("GET", "/arturo/ptt/stream/events") in routes
    assert ("POST", "/arturo/ptt/stream/end") in routes


def test_stream_gateway_has_no_relay_logic():
    src = pathlib.Path("scripts/watch_gateway.py").read_text()
    for forbidden in ("RelayManager", "PartialsEngine", "user_audio_chunk", "websocket",
                      "StreamRegistry", "scribe_v1"):
        assert forbidden not in src, f"gateway must stay a thin forward: {forbidden!r}"
    assert "127.0.0.1:5071/ptt/stream" in src, "upstream must be the loopback :5071 relay routes"
