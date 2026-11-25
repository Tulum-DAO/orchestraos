"""(Ported from the reviewed POC for the native OFFLINE fixture mode: $0, no provider socket, no real tools.)
Offline stand-in for gpt-live-1 behind the test-drive page (zero spend).

It is NOT a model. It turns a typed utterance into the documented frame sequence the real service would send
(fixtures.py builders: input transcript, delegation.created, response.event with response.created /
response.output_item.done function_call / response.completed, output transcript + speech audio) and reacts to the
client frames the REAL NativeSession sends back (function_call_output + response.create, commentary/thinking
appends). Intents are keyword rules, so the page can be clicked through end to end by gm and the reviewer.

Offline there is no speech recognition: the user types what they would say; the microphone still drives the
energy barge-in, exactly as live.
"""
import asyncio
import base64
import json
import re

import numpy as np

from . import fixtures

WORDS_PER_S = 2.6            # synthetic speech pace
FRAME_S = 0.1


def tone_pcm(seconds, seed=0):
    """A soft voiced-sounding synthetic tone (not silence), so the page plays something and energy rules fire."""
    n = int(16000 * seconds)
    t = np.arange(n) / 16000.0
    f0 = 140 + 25 * np.sin(2 * np.pi * 0.7 * t + seed)
    x = 0.5 * np.sin(2 * np.pi * np.cumsum(f0) / 16000.0) + 0.25 * np.sin(4 * np.pi * np.cumsum(f0) / 16000.0)
    env = 0.6 + 0.4 * np.abs(np.sin(2 * np.pi * 2.2 * t))
    return np.clip(np.rint(x * env * 5000), -32768, 32767).astype("<i2").tobytes()


def _intent(text):
    t = text.lower()
    if re.search(r"\b(read[- ]?only|readonly)\b", t):
        return "make_read_only"
    if re.search(r"\b(cancel|stop that task|never ?mind the task)\b", t):
        return "cancel"
    if re.search(r"\b(status|progress|done yet|finished|how'?s (the|that) task|what did gm find)\b", t):
        return "get_task_status"
    if re.search(r"\b(create|start|commission|make|have gm)\b.*\btask\b|\btask to\b", t):
        return "create_task"
    if re.search(r"\b(agents?|fleet|seats?|who'?s working|what are .* doing)\b", t):
        return "fleet_status"
    return "smalltalk"


def _title(text):
    m = re.search(r"task to (.+)", text, re.I) or re.search(r"task (?:called|named) (.+)", text, re.I)
    title = (m.group(1) if m else text).strip(" .?!")
    return title[:120] if len(title) >= 3 else "synthetic task"


def _voice_result(name, output):
    o = output if isinstance(output, dict) else {}
    if not o.get("ok", True):
        return f"I couldn't do that: {o.get('detail') or o.get('error')}."
    if name == "fleet_status":
        parts = [f"{a['seat']} is {a['status']}" + (f", {a['doing']}" if a.get("doing") else "") for a in o["agents"]]
        return "Here's the mock fleet: " + "; ".join(parts) + "."
    if name == "create_task":
        return "Okay, that task is accepted and queued. It isn't done yet."
    if name == "get_task_status":
        return f"That task is {o.get('receipt')} right now."
    if name == "task_control":
        return "Done: it's read-only now." if o.get("read_only") else f"The task is {o.get('receipt')}."
    return "Okay."


class OfflineProvider:
    """Owns the simulated provider side. `push(frame)` delivers a server frame to the session's transport;
    `on_client(frame)` sees every client frame the session sends."""

    def __init__(self, push, on_audio_done=None):
        self.push = push
        self.on_audio_done = on_audio_done
        self.last_task_id = None
        self._speaking = None            # asyncio.Task of the current utterance
        self._pending = {}               # call_id -> name
        self._n = 0
        self.started = False

    # ---- session lifecycle ----------------------------------------------------------------------------
    async def start(self):
        self.push(fixtures.started("live_offline")[0])
        self.started = True
        await self.say("Hi Shaw. Offline test drive: type what you'd say.")

    # ---- the user's turn ------------------------------------------------------------------------------
    async def user_says(self, text):
        self.interrupt()
        self.push(fixtures.input_transcript(" " + text.strip())[0])
        intent = _intent(text)
        if intent == "smalltalk":
            await self.say(_smalltalk(text))
            return
        self._n += 1
        did, rid, cid = f"del_off_{self._n}", f"resp_off_{self._n}", f"call_off_{self._n}"
        if intent == "create_task":
            name, args = "create_task", {"title": _title(text)}
        elif intent in ("make_read_only", "cancel"):
            if not self.last_task_id:
                await self.say("Which task? I haven't created one this session.")
                return
            name, args = "task_control", {"task_id": self.last_task_id, "action": intent}
        elif intent == "get_task_status":
            if not self.last_task_id:
                await self.say("There's no task yet. Want me to create one?")
                return
            name, args = "get_task_status", {"task_id": self.last_task_id}
        else:
            name, args = "fleet_status", {}
        self._pending[cid] = name
        for frame, _doc in fixtures.tool_turn(did, rid, [(cid, name, json.dumps(args))]):
            self.push(frame)
        await self.say("One sec.")            # the documented 'speech continues while work runs' (U3)

    # ---- what the session sent back ------------------------------------------------------------------
    async def on_client(self, frame):
        t = frame.get("type")
        if t == "response.item.create":
            item = frame.get("item") or {}
            cid = item.get("call_id")
            name = self._pending.pop(cid, None)
            if name is None:
                return
            try:
                out = json.loads(item.get("output") or "{}")
            except ValueError:
                out = {}
            if name == "create_task" and out.get("task_id"):
                self.last_task_id = out["task_id"]
            self._last_result = (name, out)
        elif t == "response.create" and getattr(self, "_last_result", None):
            name, out = self._last_result
            self._last_result = None
            await self.say(_voice_result(name, out))
        elif t in ("session.commentary.append", "session.thinking.append"):
            kind = t.split(".")[1]
            self.push(fixtures.appended(kind, frame.get("event_id"))[0])
            if kind == "commentary":
                await self.say(frame.get("content") or "")

    # ---- speech ------------------------------------------------------------------------------------------
    async def say(self, text):
        self.interrupt()
        self._speaking = asyncio.ensure_future(self._speak(text))

    async def _speak(self, text):
        words = text.split()
        secs = max(0.6, len(words) / WORDS_PER_S)
        pcm = tone_pcm(secs, seed=self._n)
        step = int(16000 * 2 * FRAME_S)
        try:
            for i, w in enumerate(words):
                self.push(fixtures.output_transcript(" " + w if i else w)[0])
            for k in range(0, len(pcm), step):
                self.push({"type": "session.output_audio.delta", "event_id": f"evt_off_a{k}",
                           "delta": base64.b64encode(pcm[k:k + step]).decode()})
                await asyncio.sleep(FRAME_S)
            for _ in range(10):                      # 1 s of exact-zero frames: the span ends (relay rule)
                self.push({"type": "session.output_audio.delta", "event_id": "evt_off_q",
                           "delta": base64.b64encode(b"\x00" * step).decode()})
                await asyncio.sleep(FRAME_S / 4)
        except asyncio.CancelledError:
            for _ in range(10):
                self.push({"type": "session.output_audio.delta", "event_id": "evt_off_q",
                           "delta": base64.b64encode(b"\x00" * step).decode()})
            raise

    def interrupt(self):
        if self._speaking and not self._speaking.done():
            self._speaking.cancel()

    @property
    def speaking(self):
        return bool(self._speaking and not self._speaking.done())


def _smalltalk(text):
    t = text.lower()
    if re.search(r"\b(hi|hey|hello)\b", t):
        return "Hey! Doing well. What can I do for you?"
    if "how" in t and "going" in t:
        return "Pretty good. Ready when you are."
    if re.search(r"\b(bye|goodbye|that's all)\b", t):
        return "Talk soon."
    return "Got it. Ask me about the mock fleet, or have me create a task."
