"""relay_live_bridge: the browser's Live voice mode on GPT-Live (no Gemini key)."""
import asyncio
import base64
import json

import numpy as np
from aiohttp import WSMsgType, web, ClientSession

from services.arturo import relay_live_bridge as B


def test_journal_call_id_matches_the_relay_journal():
    import hashlib
    cid = "00000000-0000-4000-8000-0000000000aa"
    assert B.journal_call_id(cid) == "vc_client_" + hashlib.sha1(cid.encode()).hexdigest()[:12]


def test_upsampler_is_3_to_2_and_seamless_across_chunks():
    t = np.arange(16000)
    sig = (8000 * np.sin(2 * np.pi * 220 * t / 16000)).astype("<i2")
    whole = B.Upsampler().feed(sig.tobytes())
    up = B.Upsampler()
    parts = b"".join(up.feed(sig[i:i + 1600].tobytes()) for i in range(0, 16000, 1600))
    assert abs(len(parts) // 2 - 24000) <= 2
    a = np.frombuffer(whole, "<i2")
    b = np.frombuffer(parts, "<i2")
    n = min(a.size, b.size)
    assert np.max(np.abs(a[:n].astype(int) - b[:n].astype(int))) <= 1   # chunked == one-shot: no edge clicks


def test_translate_maps_relay_events_to_the_browser_contract():
    assert B.translate({"type": "agent_response", "text": "Hi", "partial": True}) == \
        [("json", {"event": "transcript", "role": "arturo", "text": "Hi", "partial": True})]
    assert B.translate({"type": "agent_response", "text": "Hi there"}) == \
        [("json", {"event": "transcript", "role": "arturo", "text": "Hi there"})]
    assert B.translate({"type": "user_transcript", "text": "hello", "turn": 1}) == \
        [("json", {"event": "transcript", "role": "user", "text": "hello"})]
    assert B.translate({"type": "barge_in"}) == [("json", {"event": "interrupted"})]
    assert B.translate({"type": "assistant_end"}) == [("json", {"event": "turn_complete"})]
    assert B.translate({"type": "vendor_unavailable", "message": "no credit"}) == \
        [("json", {"event": "error", "reason": "no credit"})]
    assert B.translate({"type": "audio", "audio": base64.b64encode(b"\x01\x00").decode()}) == [("audio16k", b"\x01\x00")]
    assert B.translate({"type": "agent_speaking", "speaking": True}) == []
    assert B.translate({"type": "agent_response", "text": "  "}) == []


def _fake_relay(state):
    async def audio(req):
        state["audio"] += await req.read()
        state["headers"] = dict(req.headers)
        if state.get("refuse"):
            return web.json_response({"ok": False, "error": "vendor_unavailable", "message": "missing OPENAI_API_KEY"},
                                     status=503)
        return web.json_response({"ok": True})

    async def events(req):
        cur = int(req.query.get("cursor", 0))
        if cur == 0 and state["audio"]:
            pcm = (np.ones(1600) * 1000).astype("<i2").tobytes()
            evs = [{"type": "user_transcript", "text": "what's up"},
                   {"type": "audio", "audio": base64.b64encode(pcm).decode()},
                   {"type": "agent_response", "text": "All good."},
                   {"type": "assistant_end"}]
            return web.json_response({"ok": True, "events": evs, "cursor": 4})
        await asyncio.sleep(0.05)
        return web.json_response({"ok": True, "events": [], "cursor": cur})

    async def end(req):
        state["ended"] = req.headers.get("X-Conversation-Id")
        return web.json_response({"ok": True, "ended": True})

    app = web.Application()
    app.router.add_post("/ptt/stream/audio", audio)
    app.router.add_get("/ptt/stream/events", events)
    app.router.add_post("/ptt/stream/end", end)
    return app


async def _call(state, until_events, pages=None, mid_call=None):
    relay = web.AppRunner(_fake_relay(state)); await relay.setup()
    rsite = web.TCPSite(relay, "127.0.0.1", 0); await rsite.start()
    rport = rsite._server.sockets[0].getsockname()[1]

    async def live(req):
        ws = web.WebSocketResponse(); await ws.prepare(req)
        await B.RelayLiveSession(ws, f"http://127.0.0.1:{rport}/ptt/stream", {"X-Arturo-Principal": "fleet"},
                                 voice="marin",
                                 on_surface=(lambda r, f, c: pages.append((r, f, c))) if pages is not None else None).run()
        return ws
    gw = web.Application(); gw.router.add_get("/live", live)
    grun = web.AppRunner(gw); await grun.setup()
    gsite = web.TCPSite(grun, "127.0.0.1", 0); await gsite.start()
    gport = gsite._server.sockets[0].getsockname()[1]

    got = {"json": [], "audio": 0}
    async with ClientSession() as s:
        async with s.ws_connect(f"http://127.0.0.1:{gport}/live") as ws:
            await ws.send_str(json.dumps({"route": "/", "focusedEntity": None}))
            for frame in mid_call or []:
                await ws.send_str(json.dumps(frame))
            await ws.send_bytes(b"\x00\x00" * 3200)          # one 200 ms batch
            try:
                async with asyncio.timeout(5):
                    async for m in ws:
                        if m.type == WSMsgType.TEXT:
                            got["json"].append(json.loads(m.data))
                            if until_events(got):
                                break
                        elif m.type == WSMsgType.BINARY:
                            got["audio"] += len(m.data)
            except TimeoutError:
                pass
    await asyncio.sleep(0.2)
    await grun.cleanup(); await relay.cleanup()
    return got


def test_a_call_round_trips_through_the_relay():
    state = {"audio": b""}
    got = asyncio.run(_call(state, lambda g: any(j.get("event") == "turn_complete" for j in g["json"])))
    hello = got["json"][0]
    assert hello["event"] == "connected" and hello["user_captions"] == "server"
    assert hello["session_id"].startswith("vc_client_")
    assert {"event": "transcript", "role": "user", "text": "what's up"} in got["json"]
    assert {"event": "transcript", "role": "arturo", "text": "All good."} in got["json"]
    assert abs(got["audio"] - 4800) <= 4                      # 1600 samples @16k -> ~2400 @24k
    assert len(state["audio"]) == 6400
    assert state["headers"]["X-Arturo-Principal"] == "fleet" and state["headers"]["X-Surface"] == "web"
    assert state["ended"]                                     # hanging up ends the relay conversation


def test_a_refused_vendor_is_said_not_silent():
    state = {"audio": b"", "refuse": True}
    got = asyncio.run(_call(state, lambda g: any(j.get("event") == "error" for j in g["json"])))
    assert {"event": "error", "reason": "missing OPENAI_API_KEY"} in got["json"]


def test_the_page_reaches_the_screen_context_and_follows_navigation():
    pages = []
    state = {"audio": b""}
    asyncio.run(_call(state, lambda g: any(j.get("event") == "turn_complete" for j in g["json"]), pages=pages,
                      mid_call=[{"event": "context", "route": "/agents", "focusedEntity": "agent:ops-1"},
                                {"event": "user_turn", "text": "ignored"}]))
    assert [(r, f) for r, f, _ in pages] == [("/", None), ("/agents", "agent:ops-1")]
    assert pages[0][2] == state["headers"]["X-Conversation-Id"]      # keyed to this call (the surface sidecar)


def test_a_failing_surface_writer_never_breaks_the_call():
    def boom(*_):
        raise OSError("disk full")
    sess = B.RelayLiveSession(None, "http://x", {}, on_surface=boom)
    sess._note_page(json.dumps({"route": "/", "focusedEntity": None}))   # logged, not raised
    sess._note_page("not json")
    assert sess._page == ("/", None)
