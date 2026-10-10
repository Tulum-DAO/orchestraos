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
