"""GPT-Live vendor helpers (docs/ARTURO.md "GPT-Live"). No network: the socket facade is
exercised with a fake async connection."""
import asyncio
import json

import numpy as np
import pytest

from services.arturo import openai_live as ol


def _pcm(rms, n=1600, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    x = x / np.sqrt((x * x).mean()) * rms if rms else np.zeros(n)
    return np.clip(np.rint(x), -32768, 32767).astype("<i2").tobytes()


def test_silence_and_low_level_noise_are_not_speech():
    assert not ol.is_speech(_pcm(0))                 # GPT-Live's gaps: exact zero
    assert not ol.is_speech(_pcm(2))                 # dither
    assert not ol.is_speech(_pcm(40))                # the fade/noise cluster measured below 50


def test_speech_level_is_speech_even_in_one_20ms_window():
    assert ol.is_speech(_pcm(640))                   # measured speech p50
    frame = bytearray(_pcm(0))
    frame[0:640] = _pcm(120, n=320)                  # one 20 ms window over the floor; whole-frame RMS ~54 is under it
    assert ol.is_speech(bytes(frame))


def test_commentary_carries_delegation_id_and_is_chunked():
    long = " ".join(["word"] * 1200)
    frames = [json.loads(f) for f in ol.commentary_frames("item_X", long)]
    assert len(frames) >= 4 and all(f["delegation_id"] == "item_X" for f in frames)
    assert all(f["type"] == "session.commentary.append" and len(f["content"]) <= ol.COMMENTARY_MAX_CHARS for f in frames)
    assert " ".join(f["content"] for f in frames) == long


def test_empty_backend_reply_becomes_an_explicit_nothing_found():
    f = json.loads(ol.commentary_frames("item_Y", "  ")[0])
    assert f["content"] == "I couldn't find anything on that."


def test_session_start_is_client_delegation_16k_no_tools():
    s = ol.session_start()["session"]
    assert s["model"] == "gpt-live-1" and s["delegation"] == {"type": "client"}
    assert s["audio"]["format"] == {"type": "audio/pcm", "rate": 16000}
    assert "tools" not in s
    assert "NEVER say you did" in s["instructions"]


def test_encode_uplink():
    d = json.loads(ol.encode_uplink(b"\x01\x00"))
    assert d == {"type": "session.input_audio.append", "audio": "AQA="}


class _FakeWs:
    def __init__(self):
        self.sent = []
        self.inbox = asyncio.Queue()
        self.closed = False

    async def send(self, p):
        self.sent.append(p)

    async def close(self):
        self.closed = True
        await self.inbox.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        m = await self.inbox.get()
        if m is None:
            raise StopAsyncIteration
        return m


def test_socket_facade_send_recv_and_close_raises():
    fake = _FakeWs()

    async def connect(url, headers):
        return fake
    s = ol.AsyncWsSocket("wss://x", {"Authorization": "Bearer t"}, connect=connect)
    s.send('{"a":1}')
    assert fake.sent == ['{"a":1}']
    asyncio.run_coroutine_threadsafe(fake.inbox.put('{"type":"session.started"}'), s._loop).result(2)
    assert json.loads(s.recv())["type"] == "session.started"
    s.close()
    with pytest.raises(ConnectionError):
        s.recv()
    with pytest.raises(ConnectionError):
        s.recv()                                     # stays dead


def test_socket_facade_surfaces_a_connect_failure():
    async def connect(url, headers):
        raise OSError("403 Forbidden")
    with pytest.raises(OSError):
        ol.AsyncWsSocket("wss://x", {}, connect=connect)


# ---- public adaptations: the key comes from the product secrets route; no key never connects ----------

def test_the_key_comes_from_the_vendor_secrets_route_and_nothing_else(monkeypatch):
    from services.arturo import voice_vendor
    seen = []
    monkeypatch.setattr(voice_vendor, "_secret", lambda k: (seen.append(k), "sk-test-fake")[1])
    assert ol.api_key() == "sk-test-fake" and seen == ["OPENAI_API_KEY"]
    assert not hasattr(ol, "KEY_FILE"), "a fixed key file path came back"


def test_no_key_refuses_before_any_connect(monkeypatch):
    from services.arturo import voice_vendor
    monkeypatch.setattr(voice_vendor, "_secret", lambda k: "")
    made = []
    monkeypatch.setattr(ol, "AsyncWsSocket", lambda *a, **k: made.append(a))
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        ol.openai_socket_factory("conv_x")
    assert made == [], "a socket was opened without a key"


@pytest.mark.parametrize("accepts", ["additional_headers", "extra_headers"])
def test_connect_passes_the_auth_header_on_old_and_new_websockets(monkeypatch, accepts):
    import types
    got = {}

    async def connect(url, **kw):
        if accepts not in kw:
            raise TypeError(f"unexpected keyword {sorted(kw)}")
        got.update(url=url, headers=kw[accepts])
        return "ws"
    monkeypatch.setitem(__import__("sys").modules, "websockets", types.SimpleNamespace(connect=connect))
    assert asyncio.run(ol._ws_connect("wss://x", {"Authorization": "Bearer k"})) == "ws"
    assert got == {"url": "wss://x", "headers": {"Authorization": "Bearer k"}}


def test_socket_facade_fails_fast_once_the_server_has_closed():
    """Soak 2026-10-10 01:09 ET: after the vendor closed the socket the event loop had stopped, so send() waited out
    its 10 s timeout (stalling the uplink) and close() its 5 s, on coroutines that were never run."""
    import time
    fake = _FakeWs()

    async def connect(url, headers):
        return fake
    s = ol.AsyncWsSocket("wss://x", {}, connect=connect)
    asyncio.run_coroutine_threadsafe(fake.inbox.put(None), s._loop).result(2)      # the server ends the stream
    with pytest.raises(ConnectionError):
        s.recv()
    s._thread.join(2)
    t = time.time()
    with pytest.raises(ConnectionError):
        s.send('{"a":1}')
    s.close()
    assert time.time() - t < 0.5, f"send+close on a dead socket took {time.time() - t:.1f} s"


# --- uplink pacing (the operator 2026-10-10 ~04:40 ET: "GPT-Live not loading its first turn after 10 seconds") ---
# MEASURED on a real call: the first audio POST blocked ~3 s while the session connected; the phone then flushed
# its backlog and OpenAI answered input_audio_rate_limit_exceeded: "Send audio at no more than 1.2x real-time speed
# with bursts of at most 5 seconds" (probed verbatim). The dropped audio was their first sentence, so no turn ever came.

def _speech(sec, rms=3000):
    return _pcm(rms, n=int(16000 * sec), seed=7)


def test_pacer_sends_one_burst_then_paces_under_the_vendor_limit():
    p = ol.UplinkPacer(rate=1.15, burst_s=4.5)
    for _ in range(40):                       # 8 s of audio arriving at once (a flushed backlog)
        p.push(_speech(0.2))
    sent = sum(len(f) for f in p.take(now=0.0)) / 32000
    assert sent <= 4.5 + 1e-9, f"first burst {sent:.2f} s exceeds the 5 s vendor burst"
    later = sum(len(f) for f in p.take(now=2.0)) / 32000
    assert sent + later <= 4.5 + 2.0 * 1.15 + 1e-9, "by t, never more than one burst + t x 1.15 (the vendor rule)"
    rest = sum(len(f) for f in p.take(now=100.0)) / 32000
    assert abs(sent + later + rest - 8.0) < 1e-6, "speech is never dropped, only delayed"


def test_pacer_trims_leading_silence_from_an_over_long_backlog_but_never_speech():
    p = ol.UplinkPacer(rate=1.15, burst_s=4.5)
    for _ in range(15):
        p.push(_pcm(0, n=3200))                # 3 s of pre-speech silence buffered during connect
    for _ in range(15):
        p.push(_speech(0.2))                   # then 3 s of the operator talking
    out = p.take(now=0.0)
    speech_s = sum(len(f) for f in out if ol.is_speech(f)) / 32000
    assert abs(speech_s - 3.0) < 1e-6, "all of their speech goes in the first burst"
    assert sum(len(f) for f in out) / 32000 <= 4.5 + 1e-9


def test_socket_paces_a_flushed_backlog_instead_of_bursting_it(monkeypatch):
    fake = _FakeWs()

    async def connect(url, headers):
        return fake
    s = ol.AsyncWsSocket("wss://x", {}, connect=connect)
    try:
        s.send('{"type":"session.start"}')
        for _ in range(40):                    # 8 s of audio handed over in one go, as the relay did on a real call
            s.send(ol.encode_uplink(_speech(0.2)))
        asyncio.run_coroutine_threadsafe(fake.inbox.put('{"type":"session.started"}'), s._loop).result(2)
        import time
        time.sleep(0.3)
        appended = [m for m in fake.sent if "input_audio.append" in m]
        assert fake.sent[0] == '{"type":"session.start"}'
        assert 0 < len(appended) * 0.2 <= 5.0, f"{len(appended) * 0.2:.1f} s sent at once: over the 5 s burst"
    finally:
        s.close()


def test_arturo_greets_first_on_gpt_live():
    """the operator 2026-10-10 ~04:40 ET: on GPT-Live nothing came for 10+ s (Hume greets; GPT-Live waits for the user).
    MEASURED on the real API: a greet-on-start line in session.start instructions gives first speech ~1.9 s after
    connect; session.instructions.append after start is refused (needs a delegation_id). The relay's user-first
    guard already makes the greeting yield the moment the operator talks."""
    instr = ol.session_start()["session"]["instructions"]
    assert "greeting" in instr.lower() and "without waiting for them" in instr.lower()
    assert "if the operator speaks first" in instr.lower(), "the greeting must yield to them"


def test_audio_waits_for_session_started_because_earlier_audio_is_silently_lost():
    """MEASURED on the real API 2026-10-10: the same 6 s burst sent BEFORE session.started lost its first sentence
    ("Spin up a new agent to audit the landing page") with NO error; sent after, every word was heard."""
    fake = _FakeWs()

    async def connect(url, headers):
        return fake
    s = ol.AsyncWsSocket("wss://x", {}, connect=connect)
    try:
        s.send('{"type":"session.start"}')
        s.send(ol.encode_uplink(_speech(0.2)))
        import time
        time.sleep(0.2)
        assert not [m for m in fake.sent if "input_audio.append" in m], "audio left before session.started"
        asyncio.run_coroutine_threadsafe(fake.inbox.put('{"type":"session.started"}'), s._loop).result(2)
        assert json.loads(s.recv())["type"] == "session.started"
        time.sleep(0.2)
        assert [m for m in fake.sent if "input_audio.append" in m], "audio never released after session.started"
    finally:
        s.close()
