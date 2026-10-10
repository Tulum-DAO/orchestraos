"""OpenAI GPT-Live-1 ("openai") as a relay voice vendor (docs/ARTURO.md, "GPT-Live").

GPT-Live is the VOICE only. Every substantive turn is delegated (client delegation) to OUR CLM endpoint over
loopback, byte-for-byte as Hume calls it, so the caller stamp, the tool allowlist and the journal are unchanged.
GPT-Live gets ZERO tools. Measured facts this module is built on (scratch session 2026-10-10):
- endpoint wss://api.openai.com/v1/live/sessions; session.start first; 16 kHz PCM both ways (no resampling)
- output audio streams CONTINUOUSLY in 100 ms frames; between replies they're exact zero or dither (RMS < 50),
  speech is typically RMS ~640, so "speaking" is an ENERGY test over 20 ms windows, never frame arrival
- no interruption event, no user-final event; session.delegation.created ends a substantive turn
- session.commentary.append needs delegation_id; content up to 500 tokens per append
- websocket-client runs dropped at 80-120 s; the asyncio `websockets` client ran 8.5 min clean -> AsyncWsSocket
The key (OPENAI_API_KEY) is read per call through the same secrets route that decides whether the vendor is
available (voice_vendor._secret: <data dir>/.env.secrets, then the env), and is never logged. No key = the
vendor is listed unavailable and the socket factory refuses; nothing falls back to another source.
"""
import asyncio
import base64
import json
import os
import queue
import threading

import numpy as np

URL = "wss://api.openai.com/v1/live/sessions"
MODEL = "gpt-live-1"
KEY = "OPENAI_API_KEY"
SPEECH_RMS = float(os.environ.get("ARTURO_OPENAI_SPEECH_RMS", "60"))   # dither < 50, speech p50 ~640
WINDOW = 320                                                           # 20 ms at 16 kHz
COMMENTARY_MAX_CHARS = 1500                                            # well under 500 tokens per append
CALL_CAP_S = float(os.environ.get("ARTURO_OPENAI_CALL_CAP_S", str(20 * 60)))
CALL_WARN_S = CALL_CAP_S - 60.0

INSTRUCTIONS = (
    "You are Arturo, the operator's voice assistant. You are only the VOICE: a backend agent has all of the operator's context "
    "and tools. DELEGATE every substantive request: anything about their work, agents, the fleet, messages, "
    "approvals, memory, plans, or any action. NEVER say you did, sent, checked, started or found anything "
    "yourself, and never state a fact about their work unless the backend told you. When the backend answers, "
    "relay its answer faithfully. You may answer pure small talk directly. While waiting for the backend, one "
    "short natural acknowledgement is fine.")


def api_key():
    from services.arturo import voice_vendor
    return (voice_vendor._secret(KEY) or "").strip()


def is_speech(pcm, floor=None):
    """True if any 20 ms window of this s16le frame is above the speech floor (gm condition 1)."""
    floor = SPEECH_RMS if floor is None else floor
    n = len(pcm) // 2
    if n == 0:
        return False
    x = np.frombuffer(pcm[: n * 2], "<i2").astype(np.float32)
    k = max(1, n // WINDOW)
    w = x[: k * WINDOW].reshape(k, WINDOW) if n >= WINDOW else x.reshape(1, -1)
    return bool((np.sqrt((w * w).mean(axis=1)) > floor).any())


def encode_uplink(pcm):
    return json.dumps({"type": "session.input_audio.append", "audio": base64.b64encode(pcm).decode()})


def session_start(instructions=INSTRUCTIONS):
    return {"type": "session.start", "session": {
        "model": MODEL, "instructions": instructions,
        "audio": {"format": {"type": "audio/pcm", "rate": 16000}},
        "delegation": {"type": "client"}}}


def commentary_frames(delegation_id, text):
    """The backend's reply as session.commentary.append frames, each under the 500-token limit.
    An empty reply becomes an EXPLICIT 'nothing found' line: a vague result was reported as 'still waiting'."""
    text = (text or "").strip() or "I couldn't find anything on that."
    parts, cur = [], ""
    for word in text.split(" "):
        if cur and len(cur) + 1 + len(word) > COMMENTARY_MAX_CHARS:
            parts.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}" if cur else word
    if cur:
        parts.append(cur)
    return [json.dumps({"type": "session.commentary.append", "delegation_id": delegation_id, "content": p})
            for p in parts]


async def _ws_connect(url, headers):
    """`websockets` >= 14 takes additional_headers; 12-13 (the legacy client) take extra_headers. Public
    installs get whatever pip resolves, so try the current name first."""
    import websockets
    kw = {"max_size": None, "ping_interval": 20, "ping_timeout": 20}
    try:
        return await websockets.connect(url, additional_headers=headers, **kw)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers, **kw)


class AsyncWsSocket:
    """A sync send/recv/close facade over an asyncio `websockets` connection running in its own thread,
    so the relay's threaded reader is unchanged (gm condition 2). send() is thread-safe; recv() blocks and
    RAISES once the socket has closed, exactly as the relay expects from a dead socket."""
    _CLOSED = object()

    def __init__(self, url, headers, connect_timeout=15.0, connect=None):
        self._q = queue.Queue()
        self._loop = asyncio.new_event_loop()
        self._ws = None
        self._ready = threading.Event()
        self._err = None
        self._connect = connect
        self._thread = threading.Thread(target=self._run, args=(url, headers), daemon=True, name="openai-live-ws")
        self._thread.start()
        if not self._ready.wait(connect_timeout):
            self.close()
            raise TimeoutError("openai live: connect timed out")
        if self._err is not None:
            raise self._err

    def _run(self, url, headers):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main(url, headers))

    async def _main(self, url, headers):
        try:
            if self._connect is not None:
                self._ws = await self._connect(url, headers)
            else:
                self._ws = await _ws_connect(url, headers)
        except Exception as e:  # noqa: BLE001 — surfaced to the caller of __init__
            self._err = e
            self._ready.set()
            return
        self._ready.set()
        try:
            async for m in self._ws:
                self._q.put(m if isinstance(m, str) else m.decode())
        except Exception:
            pass
        finally:
            self._q.put(self._CLOSED)

    def send(self, payload):
        if self._ws is None:
            raise ConnectionError("openai live: not connected")
        fut = asyncio.run_coroutine_threadsafe(self._ws.send(payload), self._loop)
        fut.result(timeout=10)

    def recv(self):
        m = self._q.get()
        if m is self._CLOSED:
            self._q.put(self._CLOSED)              # every later recv() also raises
            raise ConnectionError("openai live: socket closed")
        return m

    def close(self):
        ws = self._ws
        if ws is not None:
            try:
                asyncio.run_coroutine_threadsafe(ws.close(), self._loop).result(timeout=5)
            except Exception:
                pass
        self._q.put(self._CLOSED)


def openai_socket_factory(conversation_id, **_):
    """The relay's socket factory for vendor 'openai': connect, then send session.start as the first frame."""
    key = api_key()
    if not key:
        raise RuntimeError("openai: no API key (set OPENAI_API_KEY in <data dir>/.env.secrets or the env)")
    s = AsyncWsSocket(URL, {"Authorization": "Bearer " + key})
    s.send(json.dumps(session_start()))
    return s
