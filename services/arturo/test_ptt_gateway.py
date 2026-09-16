# Tests for the watch-gateway /arturo/ptt thin proxy — the AUTHENTICATED front door. Locks the trust
# boundary (Bearer required) and that the route is wired into build_app(). The multipart-forward body
# mirrors the proven handle_upload pattern and is exercised end-to-end at the build gate / on-device.

import importlib.util
import pathlib

import pytest
from aiohttp.test_utils import make_mocked_request


def _load_gateway():
    spec = importlib.util.spec_from_file_location(
        "watch_gateway", pathlib.Path("scripts/watch_gateway.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_ptt_gateway_requires_bearer():
    mod = _load_gateway()
    req = make_mocked_request("POST", "/arturo/ptt")        # no Authorization header
    resp = await mod.handle_arturo_ptt(req)
    assert resp.status == 401


async def test_ptt_gateway_rejects_wrong_bearer(monkeypatch):
    mod = _load_gateway()
    monkeypatch.setattr(mod, "gateway_token", lambda: "the-real-token")
    req = make_mocked_request("POST", "/arturo/ptt",
                              headers={"Authorization": "Bearer wrong-token"})
    resp = await mod.handle_arturo_ptt(req)
    assert resp.status == 401


def test_ptt_gateway_route_registered():
    mod = _load_gateway()
    app = mod.build_app()
    routes = {(r.method, r.resource.canonical) for r in app.router.routes()}
    assert ("POST", "/arturo/ptt") in routes


def test_ptt_gateway_has_no_brain_logic():
    # gm requirement (msg_3590f8dc): the gateway proxy does auth + forward ONLY. The STT/brain/TTS
    # live on :5071, never in the gateway. Assert the gateway source carries none of that logic.
    src = pathlib.Path("scripts/watch_gateway.py").read_text()
    for forbidden in ("build_context", "_ptt_brain", "_ptt_stt", "_ptt_tts", "ptt_turn(",
                      "audio.transcriptions", "text-to-speech", "chat.completions"):
        assert forbidden not in src, f"gateway must not contain brain logic: {forbidden!r}"


async def test_ptt_gateway_forwards_verbatim_to_upstream(monkeypatch):
    # gm requirement (msg_3590f8dc): prove FORWARD-ONLY behaviour — a valid authed multipart is
    # forwarded to :5071/ptt and the upstream JSON is returned VERBATIM, with no gateway-side brain.
    from aiohttp import web, FormData
    from aiohttp.test_utils import TestServer, TestClient

    mod = _load_gateway()
    monkeypatch.setattr(mod, "gateway_token", lambda: "tok")

    seen = {}

    async def upstream_ptt(request):
        reader = await request.multipart()
        field = await reader.next()
        while field is not None:
            if field.name == "audio":
                seen["audio"] = await field.read()
            elif field.name == "conversation_id":
                seen["conv"] = await field.text()
            elif field.name == "turn_id":
                seen["turn"] = await field.text()
            field = await reader.next()
        return web.json_response(
            {"ok": True, "reply_text": "from-upstream", "stt_text": "heard", "audio": "QUJD"})

    up_app = web.Application()
    up_app.router.add_post("/ptt", upstream_ptt)
    up_server = TestServer(up_app)
    await up_server.start_server()
    try:
        monkeypatch.setattr(mod, "ARTURO_PTT_URL", str(up_server.make_url("/ptt")))
        gw_client = TestClient(TestServer(mod.build_app()))
        await gw_client.start_server()
        try:
            data = FormData()
            data.add_field("audio", b"AUDIOBYTES", filename="clip.m4a", content_type="audio/m4a")
            data.add_field("conversation_id", "conv-9")
            data.add_field("turn_id", "turn-9")
            resp = await gw_client.post("/arturo/ptt", data=data,
                                        headers={"Authorization": "Bearer tok"})
            assert resp.status == 200
            body = await resp.json()
            assert body["reply_text"] == "from-upstream"      # returned verbatim from :5071
            assert body["audio"] == "QUJD"
        finally:
            await gw_client.close()
    finally:
        await up_server.close()
    # the gateway forwarded exactly what the watch sent
    assert seen["audio"] == b"AUDIOBYTES"
    assert seen["conv"] == "conv-9" and seen["turn"] == "turn-9"


async def test_ptt_stream_audio_forwards_x_surface(monkeypatch):
    # item(1): the phone sends X-Surface: phone (RelayTransport.swift:108); the gateway MUST forward
    # it to :5071/ptt/stream/audio so the relay journal can attribute the call to the phone. Today
    # _stream_forward drops everything except X-Conversation-Id.
    from aiohttp import web
    from aiohttp.test_utils import TestServer, TestClient

    mod = _load_gateway()
    monkeypatch.setattr(mod, "gateway_token", lambda: "tok")

    seen = {}

    async def upstream_audio(request):
        seen["x_surface"] = request.headers.get("X-Surface")
        seen["x_conv"] = request.headers.get("X-Conversation-Id")
        return web.json_response({"ok": True})

    up_app = web.Application()
    up_app.router.add_post("/audio", upstream_audio)
    up_server = TestServer(up_app)
    await up_server.start_server()
    try:
        monkeypatch.setattr(mod, "ARTURO_PTT_STREAM_BASE", str(up_server.make_url("")).rstrip("/"))
        gw_client = TestClient(TestServer(mod.build_app()))
        await gw_client.start_server()
        try:
            resp = await gw_client.post("/arturo/ptt/stream/audio", data=b"\x00\x01" * 10,
                                        headers={"Authorization": "Bearer tok",
                                                 "X-Conversation-Id": "conv-x",
                                                 "X-Surface": "phone"})
            assert resp.status == 200
        finally:
            await gw_client.close()
    finally:
        await up_server.close()
    assert seen["x_conv"] == "conv-x"
    assert seen["x_surface"] == "phone"   # RED today: the gateway drops X-Surface


async def test_ptt_stream_audio_omits_x_surface_when_absent(monkeypatch):
    # the watch sends no X-Surface; the gateway must not invent one (upstream sees None -> defaults watch)
    from aiohttp import web
    from aiohttp.test_utils import TestServer, TestClient

    mod = _load_gateway()
    monkeypatch.setattr(mod, "gateway_token", lambda: "tok")

    seen = {}

    async def upstream_audio(request):
        seen["x_surface"] = request.headers.get("X-Surface")
        return web.json_response({"ok": True})

    up_app = web.Application()
    up_app.router.add_post("/audio", upstream_audio)
    up_server = TestServer(up_app)
    await up_server.start_server()
    try:
        monkeypatch.setattr(mod, "ARTURO_PTT_STREAM_BASE", str(up_server.make_url("")).rstrip("/"))
        gw_client = TestClient(TestServer(mod.build_app()))
        await gw_client.start_server()
        try:
            resp = await gw_client.post("/arturo/ptt/stream/audio", data=b"\x00\x01" * 10,
                                        headers={"Authorization": "Bearer tok",
                                                 "X-Conversation-Id": "conv-y"})
            assert resp.status == 200
        finally:
            await gw_client.close()
    finally:
        await up_server.close()
    assert seen["x_surface"] is None
