"""The browser's Live voice mode on a RELAY vendor (GPT-Live today) when Gemini isn't set up.

The dashboard's call button opens `/api/voice/live` -> gateway `/live`, a WebSocket whose contract was
written for Gemini Live (lib/voiceSession.ts, gemini_live_bridge.py). Without GEMINI_API_KEY that button
had nothing behind it, even with GPT-Live configured for the watch. This bridge speaks the same browser
contract and drives the stream relay on :5071 (/ptt/stream/audio|events|end), exactly as the watch does,
so the relay's vendor choice, caps, delegation to Arturo's brain and call journal all apply unchanged.

  browser -> us: first a JSON hello {route, focusedEntity, threadId?}; then binary 16 kHz mono s16le mic PCM;
                 JSON {event:"context", route, focusedEntity} when the page changes mid-call;
                 JSON {event:"user_turn"} from on-device captions (ignored here: the relay transcribes)
  us -> browser: {event:"connected", session_id, voice, user_captions:"server"}; binary 24 kHz s16le
                 (the relay's 16 kHz upsampled); {event:"transcript", text, role, partial?};
                 {event:"interrupted"} | {event:"turn_complete"} | {event:"error", reason};
                 {event:"tool_start", call_id, name, args_summary} | {event:"tool_done", call_id, name, ok, summary}

`threadId` is the Arturo thread the call was started from: it rides to the relay as X-Arturo-Thread on every
request, and the call's lines are archived into that thread (call_threads.py), where the brain sees them.

`user_captions:"server"` tells the browser the caller's words come from here, so it stops its own
recognizer (two sources would commit every user turn twice).

The page ({route, focusedEntity}) goes to `on_surface(route, focused_entity, conv_id)`: the gateway
records it as the `web` device's surface, which is what the voice brain's screen readout and its
read_screen_context tool read. It is re-stamped every SURFACE_REFRESH_S so a long call on one page
never reads as "app backgrounded" (surface.STALE_S).
"""
import asyncio
import hashlib
import json
import logging
import re
import time
import uuid

import numpy as np

log = logging.getLogger("relay_live_bridge")

_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")      # call_threads.THREAD_ID_RE

UPLINK_BATCH_BYTES = 6400        # 200 ms of 16 kHz s16le: the watch's chunk size, ~5 POSTs/s
POLL_WAIT_S = 10
SURFACE_REFRESH_S = 60           # well under surface.STALE_S (120)


def journal_call_id(cid):
    """The relay journals a conversation as vc_client_<sha1(cid)[:12]> (arturo-proxy.py), so that is the
    id the browser's call-ended card must name."""
    return "vc_client_" + hashlib.sha1(cid.encode()).hexdigest()[:12]


class Upsampler:
    """16 kHz -> 24 kHz s16le, linear, carrying the last sample and the read position across chunks so
    chunk edges don't click."""

    def __init__(self, src=16000, dst=24000):
        self.step = src / dst
        self.pos = 0.0               # read position, in input samples, relative to `prev`
        self.prev = None             # the previous chunk's last sample (index -1 of this chunk)

    def feed(self, pcm):
        x = np.frombuffer(pcm[: len(pcm) & ~1], dtype="<i2").astype(np.float32)
        if x.size == 0:
            return b""
        if self.prev is not None:
            x = np.concatenate(([self.prev], x))
            start = self.pos
        else:
            start = 0.0
        last = x.size - 1
        idx = np.arange(start, last, self.step) if start < last else np.empty(0)
        self.pos = (idx[-1] + self.step - last) if idx.size else (start - last)
        self.prev = x[-1]
        y = np.interp(idx, np.arange(x.size), x)
        return np.clip(np.round(y), -32768, 32767).astype("<i2").tobytes()


def translate(ev):
    """One relay event -> the browser frames it becomes: [("json", dict) | ("audio16k", bytes)]."""
    t = ev.get("type")
    if t == "audio":
        import base64
        try:
            return [("audio16k", base64.b64decode(ev.get("audio") or ""))]
        except Exception:
            return []
    if t == "agent_response":
        text = (ev.get("text") or "").strip()
        if not text or ev.get("echo"):
            # `echo`: the voice reading out a reply the thread already shows as text (a typed turn's answer)
            return []
        frame = {"event": "transcript", "role": "arturo", "text": text}
        if ev.get("partial"):
            frame["partial"] = True
        return [("json", frame)]
    if t == "user_partial":
        text = (ev.get("text") or "").strip()
        return [("json", {"event": "transcript", "role": "user", "text": text, "partial": True})] if text else []
    if t == "user_transcript":
        text = (ev.get("text") or "").strip()
        return [("json", {"event": "transcript", "role": "user", "text": text})] if text else []
    if t == "tool":
        # a tool Arturo's brain runs during the call: a live, collapsed tool card in the thread
        if ev.get("phase") == "start":
            return [("json", {"event": "tool_start", "call_id": ev.get("call_id"), "name": ev.get("name") or "tool",
                              "args_summary": ev.get("args_summary") or ""})]
        return [("json", {"event": "tool_done", "call_id": ev.get("call_id"), "name": ev.get("name") or "tool",
                          "ok": bool(ev.get("ok")), "summary": ev.get("summary") or ""})]
    if t == "barge_in":
        return [("json", {"event": "interrupted"})]
    if t == "assistant_end":
        return [("json", {"event": "turn_complete"})]
    if t == "vendor_unavailable":
        return [("json", {"event": "error", "reason": ev.get("message") or "the voice vendor is unavailable"})]
    return []


class RelayLiveSession:
    """One browser call on the relay. `base` is :5071's /ptt/stream; `headers` carry the gateway's own
    principal stamp (the caller is the fleet bearer, as on the Gemini path)."""

    def __init__(self, ws, base, headers, voice="", http=None, on_surface=None):
        self.ws = ws
        self.base = base.rstrip("/")
        self.cid = str(uuid.uuid4())
        self.headers = {**headers, "X-Conversation-Id": self.cid, "X-Surface": "web"}
        self.voice = voice
        self.http = http
        self.up = Upsampler()
        self.done = asyncio.Event()
        self._t0 = time.monotonic()
        self._last_audio = None
        self._seen_partial = {"user": False, "arturo": False}
        self.on_surface = on_surface
        self._page = None            # (route, focusedEntity) the browser last reported

    def _note_page(self, text):
        """A TEXT frame: the hello or a mid-call {event:"context"} carries the page; anything else is ignored."""
        try:
            msg = json.loads(text)
        except (TypeError, ValueError):
            return
        if not isinstance(msg, dict) or msg.get("event") not in (None, "context"):
            return
        tid = msg.get("threadId")
        if msg.get("event") is None and "X-Arturo-Thread" not in self.headers and isinstance(tid, str) \
                and _THREAD_ID_RE.match(tid):
            # The hello only (the first frame, before any audio is posted): a call never moves threads.
            self.headers["X-Arturo-Thread"] = tid
        route, focused = msg.get("route"), msg.get("focusedEntity")
        if not isinstance(route, str):
            return
        self._page = (route, focused if isinstance(focused, str) else None)
        self._stamp_page()

    def _stamp_page(self):
        if self.on_surface is None or self._page is None:
            return
        try:
            self.on_surface(self._page[0], self._page[1], self.cid)
        except Exception as e:  # noqa: BLE001 — screen context is best effort, never the call
            log.warning(f"relay live {self.cid}: surface stamp failed: {e}")

    async def _refresh_page(self):
        while not self.done.is_set():
            await asyncio.sleep(SURFACE_REFRESH_S)
            self._stamp_page()

    async def _send_json(self, obj):
        if not self.ws.closed:
            await self.ws.send_str(json.dumps(obj))

    async def _fail(self, reason):
        log.warning(f"relay live {self.cid}: {reason}")
        await self._send_json({"event": "error", "reason": reason})
        self.done.set()

    async def _post_audio(self, pcm):
        async with self.http.post(f"{self.base}/audio", data=pcm, headers=self.headers) as r:
            if r.status == 200:
                return True
            body = await r.json(content_type=None)
            if r.status == 410:
                self.done.set()
            else:
                await self._fail(body.get("message") or body.get("error") or f"relay refused audio ({r.status})")
            return False

    async def _uplink(self):
        buf = bytearray()
        async for msg in self.ws:
            if self.done.is_set():
                break
            if msg.type.name == "BINARY":
                buf.extend(msg.data)
                if len(buf) >= UPLINK_BATCH_BYTES:
                    pcm, buf = bytes(buf), bytearray()
                    if not await self._post_audio(pcm):
                        break
            elif msg.type.name == "TEXT":
                self._note_page(msg.data)      # the hello / a page change; user_turn captions are ignored
            elif msg.type.name in ("CLOSE", "CLOSING", "CLOSED", "ERROR"):
                break
        self.done.set()

    def _timing(self, ev):
        """Timing only (never words): when each turn's audio and captions reach us, so a caption that lags
        its speech can be told apart from one the client is slow to draw."""
        t, now = ev.get("type"), time.monotonic()
        mark = None
        if t == "audio":
            if self._last_audio is None or now - self._last_audio > 0.5:
                mark = "arturo audio starts"
            self._last_audio = now
        elif t in ("user_partial", "agent_response"):
            who = "user" if t == "user_partial" else "arturo"
            if ev.get("partial") or t == "user_partial":
                if not self._seen_partial[who]:
                    self._seen_partial[who], mark = True, f"{who} first partial"
            else:
                self._seen_partial[who], mark = False, f"{who} final ({len(ev.get('text') or '')} chars)"
        elif t == "user_transcript":
            self._seen_partial["user"], mark = False, "user final"
        elif t == "assistant_end":
            mark = "arturo turn end"
        if mark:
            log.info(f"relay live {self.cid[:8]}: +{now - self._t0:.2f}s {mark}")

    async def _downlink(self):
        cursor = 0
        while not self.done.is_set():
            try:
                async with self.http.get(f"{self.base}/events", headers=self.headers,
                                         params={"cursor": cursor, "wait": POLL_WAIT_S}) as r:
                    body = await r.json(content_type=None)
                    status = r.status
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — a dead relay ends the call loudly
                await self._fail(f"voice relay unreachable ({e})")
                return
            if status == 410:
                self.done.set()
                return
            if status != 200:
                await self._fail(body.get("error") or f"voice relay error ({status})")
                return
            cursor = body.get("cursor", cursor)
            for ev in body.get("events") or []:
                self._timing(ev)
                for kind, payload in translate(ev):
                    if self.ws.closed:
                        return
                    if kind == "audio16k":
                        out = self.up.feed(payload)
                        if out:
                            await self.ws.send_bytes(out)
                    else:
                        await self._send_json(payload)
                        if payload.get("event") == "error":
                            self.done.set()

    async def run(self):
        import aiohttp
        own = self.http is None
        if own:
            self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=POLL_WAIT_S + 15))
        try:
            await self._send_json({"event": "connected", "session_id": journal_call_id(self.cid),
                                   "voice": self.voice, "user_captions": "server", "engine": "relay"})
            tasks = [asyncio.create_task(self._uplink()), asyncio.create_task(self._downlink()),
                     asyncio.create_task(self._refresh_page())]
            await self.done.wait()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            try:
                async with self.http.post(f"{self.base}/end", headers=self.headers) as r:
                    await r.read()
            except Exception as e:  # noqa: BLE001
                log.warning(f"relay live {self.cid}: end failed: {e}")
            if own:
                await self.http.close()
            if not self.ws.closed:
                await self.ws.close()
