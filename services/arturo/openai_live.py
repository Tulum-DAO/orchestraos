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
import collections
import base64
import concurrent.futures
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
    "short natural acknowledgement is fine. You start the conversation: the moment the session begins, say one "
    "short casual greeting to the operator (under 6 words) without waiting for them. If the operator speaks first, "
    "answer them instead.")


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


def session_start(instructions=INSTRUCTIONS, voice="marin"):
    """The voice is FIXED for the session (OpenAI voice-websockets guide), so a pick applies on the next call."""
    return {"type": "session.start", "session": {
        "model": MODEL, "instructions": instructions,
        "audio": {"format": {"type": "audio/pcm", "rate": 16000}, "output": {"voice": voice}},
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


APPEND_PREFIX = '{"type": "session.input_audio.append"'
PCM_BYTES_PER_S = 16000 * 2


class UplinkPacer:
    """Keeps uplink audio inside OpenAI's input limit (probed verbatim 2026-10-10: "Send audio at no more than 1.2x
    real-time speed with bursts of at most 5 seconds"), with margin: a token bucket of `burst_s` audio-seconds that
    refills at `rate`. Over the limit OpenAI DROPS the audio: on one measured call, the operator's whole first sentence.
    Audio is delayed, never dropped, except that when the queue holds more than one burst, the OLDEST NON-SPEECH
    frames go first (pre-speech silence buffered while the session connected). Speech is never dropped."""

    def __init__(self, rate=1.15, burst_s=4.5):
        self.rate, self.burst_s = rate, burst_s
        self._q = collections.deque()          # (pcm bytes, frame) in arrival order
        self._tokens, self._t = burst_s, None
        self._lock = threading.Lock()

    def push(self, pcm, frame=None):
        with self._lock:
            self._q.append((pcm, frame))
            excess = sum(len(p) for p, _ in self._q) / PCM_BYTES_PER_S - self.burst_s
            if excess > 0:
                keep = collections.deque()
                for p, f in self._q:
                    if excess > 0 and not is_speech(p):
                        excess -= len(p) / PCM_BYTES_PER_S
                        continue
                    keep.append((p, f))
                self._q = keep

    def take(self, now):
        """Frames that may be sent at `now` (monotonic seconds), oldest first."""
        out = []
        with self._lock:
            if self._t is not None:
                self._tokens = min(self.burst_s, self._tokens + (now - self._t) * self.rate)
            self._t = now
            while self._q:
                cost = len(self._q[0][0]) / PCM_BYTES_PER_S
                if cost > self._tokens + 1e-9:
                    break
                self._tokens -= cost
                p, f = self._q.popleft()
                out.append(f if f is not None else p)
        return out

    def pending(self):
        with self._lock:
            return bool(self._q)


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
        self._done = threading.Event()               # set when the event loop's stream has ended
        self._pacer = UplinkPacer()                  # audio appends go out under the vendor's input limit
        self._wake = None                            # asyncio.Event, created on the loop
        self._started = None                         # asyncio.Event: OpenAI confirmed session.started
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
        self._wake = asyncio.Event()
        self._started = asyncio.Event()
        pump = asyncio.ensure_future(self._pump())
        self._ready.set()
        try:
            async for m in self._ws:
                m = m if isinstance(m, str) else m.decode()
                if not self._started.is_set() and '"session.started"' in m:
                    self._started.set()              # audio before this is silently discarded by OpenAI
                self._q.put(m)
        except Exception:
            pass
        finally:
            pump.cancel()
            self._done.set()
            self._q.put(self._CLOSED)

    async def _pump(self):
        """Send paced audio appends from the pacer; sleep until more is due or more arrives."""
        import time as _time
        await self._started.wait()                   # MEASURED: audio sent before session.started is lost
        while True:
            for frame in self._pacer.take(_time.monotonic()):
                try:
                    await self._ws.send(frame)
                except Exception:  # noqa: BLE001 — the socket died; the reader's socket_down owns recovery
                    return
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.05 if self._pacer.pending() else 1.0)
            except asyncio.TimeoutError:
                pass

    def _run_on_loop(self, make_coro, timeout):
        """Run a coroutine on the socket's loop. Once the stream has ended the loop stops, and a coroutine handed to
        it would never run: fail FAST (soak 2026-10-10: send waited 10 s and close 5 s on a dead socket)."""
        if self._done.is_set():
            raise ConnectionError("openai live: socket closed")
        fut = asyncio.run_coroutine_threadsafe(make_coro(), self._loop)
        waited = 0.0
        while True:
            try:
                return fut.result(timeout=0.05)
            except concurrent.futures.TimeoutError:
                waited += 0.05
                if self._done.is_set() and not fut.done():
                    fut.cancel()
                    raise ConnectionError("openai live: socket closed") from None
                if waited >= timeout:
                    raise

    def send(self, payload):
        if self._ws is None:
            raise ConnectionError("openai live: not connected")
        if isinstance(payload, str) and payload.startswith(APPEND_PREFIX):
            # audio: queued and paced on the loop, never blocking the caller (an uplink POST)
            if self._done.is_set():
                raise ConnectionError("openai live: socket closed")
            try:
                pcm = base64.b64decode(json.loads(payload)["audio"])
            except Exception:  # noqa: BLE001 — not ours to judge: send it as-is, unpaced
                pcm = None
            if pcm is not None:
                self._pacer.push(pcm, payload)
                self._loop.call_soon_threadsafe(self._wake.set)
                return
        self._run_on_loop(lambda: self._ws.send(payload), 10)

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
                self._run_on_loop(ws.close, 5)
            except Exception:  # noqa: BLE001 — closing an already-dead socket is a no-op
                pass
        self._q.put(self._CLOSED)


def openai_socket_factory(conversation_id, **_):
    """The relay's socket factory for vendor 'openai': connect, then send session.start as the first frame."""
    key = api_key()
    if not key:
        raise RuntimeError("openai: no API key (set OPENAI_API_KEY in <data dir>/.env.secrets or the env)")
    s = AsyncWsSocket(URL, {"Authorization": "Bearer " + key})
    from services.arturo import voice_choice as _voice_choice
    s.send(json.dumps(session_start(voice=_voice_choice.get_voice("openai") or _voice_choice.OPENAI_DEFAULT)))
    return s
