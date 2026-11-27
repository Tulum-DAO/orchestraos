"""Native GPT-Live voice service: one WebSocket per call, GPT-Live's own Responses backend calling ARTURO'S REAL TOOLS
(Shaw 10-15: "fully build this parallel path to run on the iOS app when GPT-Live is connected"; gm msg_0aec5c71).

Ported from the reviewed test drive (poc/gptlive_native/testdrive/server.py) Shaw used on 10-14/10-15. Differences:
  - many calls, not one; no simulator, no per-run approval file (spend: spend.CallSpend + the voice daily cap);
  - the caller is the gateway's VERIFIED principal (X-Arturo-Principal + X-Arturo-Stamp, the proxy's own rule);
    a forwarded request (Funnel / proxy) or an unverified caller is refused: native calls need full tools;
  - Arturo's real tools (tools.ArturoTools) and the CLAIM GATE (claim_gate.ClaimGate) on his audio;
  - flag ARTURO_NATIVE_GPTLIVE default OFF: with it off every call is refused (503).

Client protocol v0 = the test page's (sent to ios-watch-dev msg_93e79510): BINARY = 16 kHz mono s16le PCM both ways;
client TEXT {type:start, settle:"fast"|"wait"} then {type:disconnect}; server TEXT state / transcript{who,text,new,
withdrawn?} / barge / tools / tick / notice / refused / stopped.
"""
import argparse
import asyncio
import base64
import collections
import json
import logging
import os
import queue
import re
import threading
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from . import call_context, claim_gate, native_config, protocol, spend
from .. import host_page, tracker_snapshot
from .offline import LocalTransport, OfflineTools
from .session import RUNNING, Driver, NativeSession, TransportDown
from .tool_ledger import ToolLedger
from .tools import ArturoTools

log = logging.getLogger("arturo-native")
ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR") or Path(__file__).resolve().parents[3])
DEFAULT_PORT = int(os.environ.get("ARTURO_NATIVE_PORT", "5076"))
BARGE_S = 0.3              # caller speech over Arturo before we ask "did he stop?"
BARGE_STOP_S = 0.4         # GPT-Live's own speech stopped this long after our cue: a real barge
BARGE_GIVEUP_S = 1.5       # he kept talking: the cue was noise (Shaw 10-14: "a small noise did NOT stop Arturo. Ideal")
# F-TD3-2 (gm msg_07a44324: every barge silent within 1.0 s; test drive 3 talked on 5.4 s): if GPT-Live has NOT stopped
# BARGE_FORCE_S after our cue and the caller kept talking (>= BARGE_SUSTAIN_S of speech since the cue: not a noise),
# the server mutes Arturo's audio + caption itself until that utterance ends. Cue 0.3 s + force 0.6 s = silent < 1.0 s.
BARGE_FORCE_S = 0.6
BARGE_SUSTAIN_S = 0.4
# Shaw 10-16 (ios msg_a562c821, verbatim): "I want it to natively play its full interruption audio while I am
# speaking." Call native-86d00024c532 cut Arturo 23 times (9 forced mutes + 14 held-audio drops) into ~0.3 s pieces.
# Default now: the server never cuts Arturo's audio; GPT-Live's own judgement decides when he stops (it does: test
# drive 10-14), and 'barge' is information for the client (cut: false). =1 restores the F-TD3-2 cut (the undo).
FORCED_BARGE = os.environ.get("ARTURO_NATIVE_FORCED_BARGE", "0") == "1"
_OWNER_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
CONTEXT_PAGES = {"Approvals", "Agents", "Arturo", "Projects", "Pipeline"}     # ios build 314 tab names
CONTEXT_DEVICES = {"iphone", "ipad", "quest", "watch", "mac"}
CONTEXT_ENABLED = os.environ.get("ARTURO_NATIVE_CONTEXT", "0") == "1"
CONTEXT_MIN_GAP_S = 2.0
CONTEXT_MAX_SENDS = 10
_OPENING_UNREAD = {"budget": None, "reread": None, "reread_result": None, "source": None, "short": None,
                   "skipped": None}   # source None: no loader ran (flag off) or it never answered (skipped says why)
OPENING_WAIT_S = 6.0          # the thread above the call loads while the provider connects; never holds a call longer
# Arturo used from inside another app (the finance tracker's widget; services/arturo/host_page.py): its page is sent as
# its own appends, capped separately (a page and a filter change each resend it), plus one finances snapshot a call.
HOST_CONTEXT_MAX_SENDS = 12
_AGENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
MAX_CALLS = int(os.environ.get("ARTURO_NATIVE_MAX_CALLS", "2"))   # review M7: concurrent calls share one daily cap
# A start whose backend config the provider rejects leaves a session with NO backend that still promises to check things
# (probe 2026-11-26, reasoning 'minimal' on gpt-6-luna). Arturo says so once, then the call ends; at most this long.
BACKEND_REJECTED_SAY_S = 8.0
BACKEND_REJECTED_REASON = "Arturo's tools are not available: the voice service rejected the backend setup"
BACKEND_REJECTED_LINE = ("Your backend is not available in this call, so you cannot check or do anything. Tell Shaw in one "
                         "short sentence that his tools are not working right now and that you are ending the call.")
NO_INPUT_S = 6.0           # gm msg_5b124db0: a live call this old with no input tells the client why it is silent
DOWNLINK_WARN_S = float(os.environ.get("ARTURO_NATIVE_DOWNLINK_WARN_S", "2.0"))


# ---- who is calling (the proxy's rule, arturo-proxy.py _forwarded + _stamped_principal) ---------------------------
def forwarded(headers):
    names = {k.lower() for k in headers.keys()}
    return "forwarded" in names or "tailscale-funnel-request" in names or any(n.startswith("x-forwarded-") for n in names)


def verified_principal(headers, orchestra_dir=ORCHESTRA_DIR, verify=None):
    """'fleet' or 'owner:<id>' when the gateway stamped it with this install's secret; else None."""
    if forwarded(headers):
        return None
    p = (headers.get("X-Arturo-Principal") or "").strip()
    if p != "fleet" and not (p.startswith("owner:") and _OWNER_ID_RE.fullmatch(p[len("owner:"):])):
        return None
    if verify is None:
        import sys
        root = str(Path(__file__).resolve().parents[3])
        if root not in sys.path:
            sys.path.insert(0, root)
        from scripts import arturo_stamp
        return p if arturo_stamp.verify(orchestra_dir, headers.get(arturo_stamp.HEADER)) else None
    return p if verify(headers) else None


# ---- audio cadence (per call) ------------------------------------------------------------------------------------
TURN_GAP_S = 1.5            # a longer gap between audio frames is a turn boundary, not a stutter: not counted


def _backend_rejected(err):
    """The provider refused the Responses delegation config (DOC_PRIMARY_WS error {code, param}): the call has no backend."""
    return err.get("code") == "responses_validation_failed" or str(err.get("param") or "").startswith("session.delegation")


def _gap(gaps, last):
    now = time.monotonic()
    if last is not None and now - last < TURN_GAP_S:
        gaps.append(now - last)
    return now


def _utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _gap_stats(gaps):
    if not gaps:
        return {"n": 0, "p50_ms": None, "p90_ms": None, "max_ms": None, "over_150ms": 0}
    g = sorted(gaps)
    pick = lambda q: round(g[min(len(g) - 1, int(q * len(g)))] * 1000)
    return {"n": len(g), "p50_ms": pick(0.5), "p90_ms": pick(0.9), "max_ms": round(g[-1] * 1000),
            "over_150ms": sum(1 for x in g if x > 0.15)}


def _gap_line(st):
    return f"{st['p50_ms']}/{st['p90_ms']}/{st['max_ms']}ms"


class DownlinkPacer:
    """Keep native PCM delivery at real time without dropping accumulated audio.

    GPT-Live may produce frames faster than wall time, and ``Driver.pump`` can
    drain a socket backlog in a single tick.  The old sender forwarded that
    backlog immediately, letting the phone build an even larger playback queue.
    This pacer deliberately moves that backlog to the server.  It never catches
    up faster after a slow send: the next due time starts from the actual send
    time, not an old, overdue deadline.
    """
    def __init__(self, conversation_id, warn_s=DOWNLINK_WARN_S, clock=None, sleeper=None):
        self.conversation_id = conversation_id
        self.warn_s = warn_s
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or asyncio.sleep
        self.next_due = None
        self.queued_s = self.max_queued_s = self.paced_wait_s = 0.0
        self.frames = 0
        self._warned = False

    def enqueue(self, pcm):
        self.queued_s += len(pcm) / 32000.0
        self.max_queued_s = max(self.max_queued_s, self.queued_s)
        if self.warn_s > 0 and self.queued_s >= self.warn_s and not self._warned:
            self._warned = True
            log.warning("native %s: DOWNLINK-BACKLOG queued_s=%.2f threshold_s=%.2f",
                        self.conversation_id, self.queued_s, self.warn_s)

    async def await_turn(self, pcm):
        now = self.clock()
        due = max(self.next_due if self.next_due is not None else now, now)
        wait_s = max(0.0, due - now)
        if wait_s:
            self.paced_wait_s += wait_s
            await self.sleeper(wait_s)
        # If a scheduler or socket pause made us late, schedule from the real
        # send instant.  Sending a burst to "catch up" recreates the phone lag.
        sent_at = self.clock()
        self.next_due = max(due, sent_at) + len(pcm) / 32000.0
        self.queued_s = max(0.0, self.queued_s - len(pcm) / 32000.0)
        if self._warned and self.queued_s < self.warn_s:
            self._warned = False

    def report(self):
        return {"queued_audio_s": round(self.queued_s, 3),
                "max_queued_audio_s": round(self.max_queued_s, 3),
                "paced_wait_s": round(self.paced_wait_s, 3),
                "frames": self.frames, "warn_threshold_s": self.warn_s}


# ---- the provider socket -----------------------------------------------------------------------------------------
class OpenAITransport:
    """Driver transport over the relay's own openai_live.AsyncWsSocket (paced uplink, never sends audio before
    session.started, logs and closes on a pump failure). send(str); recv() -> str|None (non-blocking); close()."""

    def __init__(self):
        from services.arturo import openai_live
        key = openai_live.api_key()
        if not key:
            raise RuntimeError("no OpenAI key (~/.secrets/openai.env)")
        self.ws = openai_live.AsyncWsSocket(protocol.URL, {"Authorization": "Bearer " + key})
        self._q = queue.Queue()
        threading.Thread(target=self._reader, daemon=True, name="native-reader").start()

    def _reader(self):
        try:
            while True:
                self._q.put(self.ws.recv())
        except Exception:  # noqa: BLE001 — closed; recv() reports it
            self._q.put(None)

    def send(self, payload):
        try:
            self.ws.send(payload)
        except Exception as e:  # noqa: BLE001
            raise TransportDown(type(e).__name__) from None

    def recv(self):
        try:
            m = self._q.get_nowait()
        except queue.Empty:
            return None
        if m is None:
            self._q.put(None)
            raise TransportDown("closed")
        return m

    def close(self):
        self.ws.close()

    def stats(self):
        """Did our audio leave for OpenAI? (AsyncWsSocket.stats: appends_sent, pending_s, pump_alive) plus started:
        appends wait for session.started, so 0 appends with started False is a late session, not a dead pump."""
        st = dict(self.ws.stats())
        ev = getattr(self.ws, "_started", None)
        st["started"] = bool(ev is not None and ev.is_set())
        return st


# A question to gm never holds the call (Shaw's 18:3x call, 2026-11-26: gm_command polled gm's reply for 25 s, three
# times, and the voice went silent each time). After DETACH_AFTER_S the call hears DETACHED at once; gm's answer, if
# it lands while the call is up, comes in as a note (late=), and gm's own "the answer comes to your chat" is dropped.
DETACH_TOOLS = frozenset({"gm_command"})
DETACH_AFTER_S = 1.5
DETACHED = {"ok": True, "result": "Sent to gm; gm has not answered yet. Tell the operator that once, in a few words "
                                  "(gm's answer comes into this call when it lands, or to the chat after it), then "
                                  "keep answering him yourself. Never fill the air with 'still on it'."}
_GM_ANSWER = "gm says:"


class ToolRunner:
    """Runs Arturo's tools OFF the audio loop. The session calls execute() on every pump; the first call for a
    tool_call_id starts it on a worker thread and returns RUNNING, later calls return RUNNING until the result is in,
    then the result. (Before this, a 120 s spawn_agent ran inside the event loop and froze every call's audio.)
    Idempotency stays in ArturoTools/ToolLedger: a call id runs at most once there whatever happens here.
    A DETACH_TOOLS call is handed over after DETACH_AFTER_S instead (see DETACHED)."""

    def __init__(self, tools, pool, late=None, clock=time.monotonic):
        self.tools, self.pool = tools, pool
        self.ledger = tools.ledger
        self.late, self.clock = late, clock
        self._futs, self._started, self._detached = {}, {}, set()

    def new_turn(self, conversation_id):
        self.tools.new_turn(conversation_id)

    def execute(self, binding, tool_call_id, name, arguments_json):
        if tool_call_id in self._detached:          # handed over stays handed over (codex review of #673): the
            return dict(DETACHED)                    # session re-polls a held delegation; never a second answer
        fut = self._futs.get(tool_call_id)
        if fut is None:
            fut = self._futs[tool_call_id] = self.pool.submit(self.tools.execute, binding, tool_call_id, name,
                                                              arguments_json)
            self._started[tool_call_id] = self.clock()
        if not fut.done():
            if name in DETACH_TOOLS and self.clock() - self._started[tool_call_id] >= DETACH_AFTER_S:
                if tool_call_id not in self._detached:
                    self._detached.add(tool_call_id)
                    fut.add_done_callback(self._answer_late)
                return dict(DETACHED)
            return RUNNING
        try:
            return fut.result()
        except Exception as e:  # noqa: BLE001 — a tool crash is the model's refusal, never the call's death
            log.error(f"native {binding.conversation_id}: tool {name} raised {e!r}")
            return {"ok": False, "error": "TOOL_ERROR", "detail": type(e).__name__}

    def _answer_late(self, fut):
        """A handed-over call finished: gm's real answer goes into the call as a note; anything else was said already."""
        try:
            text = str((fut.result() or {}).get("result") or "").strip()
        except Exception:  # noqa: BLE001 — the call already heard "sent"
            return
        if self.late is not None and text.startswith(_GM_ANSWER):
            self.late(" ".join(text[len(_GM_ANSWER):].split()))


class _Observer:
    def __init__(self, call):
        self.call = call

    def record(self, direction, frame):
        self.call.on_frame(direction, frame)


# ---- one call ----------------------------------------------------------------------------------------------------
class NativeCall:
    def __init__(self, server, sock, principal, device):
        self.server, self.sock = server, sock
        self.principal, self.device = principal, device
        self.mode = "live"
        self.provider = None                                       # offline: the simulator
        self._use(server.tools, ToolRunner(server.tools, server.pool, late=self._late_note))
        self.spend = spend.CallSpend(server.usage_store, native_config.BACKEND_MODEL)
        self.gate = claim_gate.ClaimGate(
            is_backed=lambda kind: self.toolset.backed(self.binding.conversation_id, self.session.turn_started_epoch,
                                                       kind),
            enabled=native_config.claim_gate_enabled())
        self.out = asyncio.Queue()
        self.transport = self.driver = None
        self.t0 = None
        self.closed = False
        self.close_reason = None
        # ``close`` can be requested concurrently by the socket loop, the
        # provider pump, and the spend ticker.  The finalizer must outlive an
        # individual waiter: ``run`` cancels its ticker while unwinding a
        # closed websocket, and cancelling a ticker that is awaiting teardown
        # must never strand a MAX_CALLS entry or skip the call report.
        self._close_task = None
        self.arturo_text = self.user_text = ""
        self._arturo_row_done = self._user_row_done = True
        self._was_speaking = False
        self._uplink_speech_s = 0.0
        self._barge_cue = None
        self._cue_speech_s = 0.0
        self._force_muted = False
        self.forced_barges = 0
        self._out_speech_t = 0.0
        # 10-16 call native-c2e8af2f7a10: OpenAI billed 0.0 s and nothing here could say where the audio stopped.
        self.up_frames = self.up_bytes = self.up_pre_start = 0
        self.up_speech_s = 0.0
        self.up_first_s = None
        self.no_input = None             # None | "no_audio" | "provider_not_ready" | "provider_not_hearing"
        self._no_input_checked = False
        self.backend_rejected = None     # "<code>: <message>" once the provider rejected the delegation config
        self._backend_rejected_at = None
        self._backend_rejected_said = False
        self._final_stats = None
        # ios msg_82c61732: phone underruns 157-653 ms. Provider delta arrival vs our send to the phone, per call.
        self._in_t = self._out_t = None
        self._in_gaps, self._out_gaps = [], []
        self._out_events, self._audio_end_at = [], []                # UTC, for matching the phone's underrun log
        self._context = None                                       # last screen context sent to GPT-Live
        self._context_pending = None                               # latest wanted, not yet sent
        self._context_sent_at, self._context_sends = 0.0, 0
        # The thread above this call and the chat that arrives during it (call_context; Shaw 2026-11-25).
        self.chat_agent = call_context.default_chat_agent()
        self.opening_tokens = 0
        self.chat_named = False     # the start frame named an agent: that agent's chat opens the call
        self.page_thread = None     # the start frame named the standalone Arturo page's thread: that thread does
        self.opening_info = {**call_context.describe(""), **_OPENING_UNREAD}
        self.chat_feed = None
        self._feed_future = None
        self._feed_next = 0.0
        self._feed_notes = collections.deque()
        self.feed_sent = 0
        self._host = self._host_pending = None                     # the host app's page: sent / latest wanted
        self._host_sends = 0
        self._snapshot = None                                      # None | "fetching" | text to send | "done"
        self.downlink = DownlinkPacer(self.binding.conversation_id)

    def _use(self, toolset, boundary):
        self.toolset = toolset
        self.binding = toolset.bind(self.principal, self.device)
        self.session = NativeSession(self.binding, boundary, settle=native_config.SETTLE_FAST)

    # ---- plumbing --------------------------------------------------------------------------------------------
    def emit(self, msg):
        if isinstance(msg, (bytes, bytearray)):
            self.downlink.enqueue(msg)
        self.out.put_nowait(msg)

    async def _sender(self):
        while True:
            msg = await self.out.get()
            if msg is None:
                return
            try:
                if isinstance(msg, (bytes, bytearray)):
                    await self.downlink.await_turn(msg)
                    await self.sock.send_bytes(msg)
                    self.downlink.frames += 1
                    n0 = len(self._out_gaps)
                    self._out_t = _gap(self._out_gaps, self._out_t)
                    if len(self._out_gaps) > n0 and self._out_gaps[-1] > 0.15:
                        self._out_events.append([_utc(), round(self._out_gaps[-1] * 1000)])
                else:
                    await self.sock.send_str(json.dumps(msg))
                    if msg.get("type") == "audio_end":
                        self._audio_end_at.append(_utc())
            except (ConnectionResetError, RuntimeError):
                return

    def on_frame(self, direction, frame):
        if direction != "in":
            if frame.get("type") in ("response.item.create", "response.create"):
                self.emit(self.tools_snapshot())
            return
        t = frame.get("type")
        now = time.monotonic()
        if t == "session.output_audio.delta":
            self._in_t = _gap(self._in_gaps, self._in_t)
            try:
                pcm = base64.b64decode(frame.get("delta") or "")
            except Exception:  # noqa: BLE001
                pcm = b""
            if pcm:
                from services.arturo import openai_live
                if openai_live.is_speech(pcm):
                    self._out_speech_t = now
                if not self._force_muted:                 # a forced barge: the rest of this utterance never plays
                    self.gate.feed_audio(pcm, now)
            speaking = self.session.assistant_speaking
            if self._was_speaking and not speaking:
                self._end_utterance(now)
            self._was_speaking = speaking
        elif t == "session.output_transcript.delta":
            fresh = not self.session.assistant_speaking and self._arturo_row_done
            if fresh:
                self.arturo_text = ""
            self._arturo_row_done = False
            self._user_row_done = True
            if self._force_muted:
                return
            was_blocked = self.gate.blocked
            self.gate.feed_transcript(frame.get("delta") or "")
            if self.gate.blocked and not was_blocked:
                log.warning(f"native {self.binding.conversation_id}: CLAIM-UNBACKED {self.gate.blocked_text[-120:]!r}")
                self.emit({"type": "transcript", "who": "arturo", "text": "", "new": False, "withdrawn": True})
            if not self.gate.blocked:
                self.arturo_text += frame.get("delta") or ""
                self.emit({"type": "transcript", "who": "arturo", "text": self.arturo_text.strip(), "new": fresh})
        elif t == "session.input_transcript.delta":
            self.arturo_text = ""
            self._arturo_row_done = True
            fresh = self._user_row_done
            if fresh:
                self.user_text = ""
            self._user_row_done = False
            self.user_text += frame.get("delta") or ""
            self.spend.activity()
            self.emit({"type": "transcript", "who": "user", "text": self.user_text.strip(), "new": fresh})
        elif t == "error":
            err = frame.get("error") or {}
            if _backend_rejected(err):
                self._reject_backend(err, now)
            else:
                self.emit({"type": "notice", "text": f"provider error: {err.get('code')}"})
        reason = self.spend.on_frame(frame) if self.mode == "live" else None
        if reason:
            asyncio.ensure_future(self.close(reason))

    def _end_utterance(self, now):
        for pcm in self.gate.end_utterance(now):
            self.emit(pcm)
        self.emit({"type": "audio_end"})                  # ios item 1: the client's turn-end signal
        self._force_muted = False
        self._arturo_row_done = True
        if self._backend_rejected_at is not None:
            self._backend_rejected_said = True
        line = self.gate.take_correction()
        if line and self.driver is not None:
            try:
                self.driver.send(protocol.commentary_append(line))
            except TransportDown:
                pass

    def _reject_backend(self, err, now):
        """Once: tell the caller (visible notice + one spoken line), then _check_backend_rejected ends the call."""
        if self.backend_rejected is not None:
            return
        self.backend_rejected = f"{err.get('code')}: {str(err.get('message') or '')[:200]}"
        self._backend_rejected_at = now
        log.warning(f"native {self.binding.conversation_id}: BACKEND-REJECTED {self.backend_rejected!r}")
        self.emit({"type": "notice", "code": "backend_rejected", "text": BACKEND_REJECTED_REASON})
        if self.driver is not None:
            try:
                self.driver.send(protocol.thinking_append(BACKEND_REJECTED_LINE))
            except TransportDown:
                pass

    async def _check_backend_rejected(self, now):
        """End a backend-less call once Arturo has said so, or BACKEND_REJECTED_SAY_S after the rejection."""
        if self._backend_rejected_at is None or self.closed:
            return False
        if not self._backend_rejected_said and now - self._backend_rejected_at < BACKEND_REJECTED_SAY_S:
            return False
        await self.close(BACKEND_REJECTED_REASON)
        return True

    def tools_snapshot(self):
        conv = self.binding.conversation_id
        calls = [{"call_id": c["tool_call_id"], "name": c["name"], "args": json.loads(c["args_json"]),
                  "ok": json.loads(c["result_json"]).get("ok"), "dup_hits": c["duplicate_hits"],
                  "task_id": c["task_id"]}
                 for c in self.toolset.ledger.calls_for(conv, 12)]
        refused = [t for t in self.toolset.trace
                   if t.get("decision") == "refused" and t.get("conversation_id") == conv][-5:]
        return {"type": "tools", "calls": calls[-12:], "refused": refused, "claims_blocked": self.gate.blocks[-3:]}

    # ---- lifecycle ---------------------------------------------------------------------------------------------
    async def run(self):
        sender = asyncio.ensure_future(self._sender())
        ticker = None
        try:
            async for msg in self.sock:
                if msg.type == WSMsgType.BINARY:
                    self._uplink(bytes(msg.data))
                elif msg.type == WSMsgType.TEXT:
                    try:
                        d = json.loads(msg.data)
                    except ValueError:
                        continue
                    k = d.get("type")
                    if k == "start" and self.t0 is None:
                        agent = d.get("agent")
                        if isinstance(agent, str) and _AGENT_ID_RE.fullmatch(agent):
                            self.chat_agent = agent          # the chat of the page the call was placed from
                            self.chat_named = True
                        thread = d.get("thread")
                        if thread is not None and not self.chat_named:
                            if isinstance(thread, str) and call_context.thread_context.THREAD_ID_RE.fullmatch(thread):
                                self.page_thread = thread    # the standalone Arturo page's own thread
                            else:
                                log.warning(f"native: thread id refused: {str(thread)[:80]!r}")
                        if d.get("mode") == "offline":
                            self.mode = "offline"
                            self._use(self.server.offline_tools, self.server.offline_tools)
                        if d.get("settle") == "wait":
                            self.session.settle = native_config.SETTLE
                        if not await self._start():
                            break
                        ticker = asyncio.ensure_future(self._tick())
                    elif k == "say" and self.mode == "offline" and str(d.get("text", "")).strip():
                        self.session.caller_spoke()
                        await self.provider.user_says(str(d["text"])[:400])
                    elif k == "context":
                        self._on_context(d)
                    elif k == "disconnect":
                        await self.close("you hung up")
                        break
                if self.closed:
                    break
        finally:
            if ticker:
                ticker.cancel()
            if not self.closed:
                await self.close("socket closed")
            self.out.put_nowait(None)
            await sender

    async def _start(self):
        if len(self.server.calls) >= MAX_CALLS:
            self.emit({"type": "refused", "reason": f"native GPT-Live is busy ({MAX_CALLS} calls already live)"})
            return False
        cid = self.binding.conversation_id
        self.server.calls[cid] = self             # reserve the slot BEFORE any await (review r1 #1: MAX_CALLS race)
        ok = False
        try:
            ok = await self._open()
        finally:
            if not ok:
                self.server.calls.pop(cid, None)
        return ok

    async def _connect(self):
        """The provider connect blocks up to 15 s (AsyncWsSocket): on its own pool, or every call's audio stalls.
        If this handler is cancelled mid-connect (server shutdown), the late socket is closed, never leaked."""
        cf = self.server.connect_pool.submit(self.server.transport_factory)
        try:
            return await asyncio.wrap_future(cf)
        except asyncio.CancelledError:
            def _close_late(f):
                if not f.cancelled() and f.exception() is None:
                    try:
                        f.result().close()
                    except Exception:  # noqa: BLE001 — best effort on a call nobody holds
                        pass
            cf.add_done_callback(_close_late)
            raise

    async def _open(self):
        threaded = call_context.enabled()
        # what opens the call (gm msg_e7402cbb): the page's own thread, else the named agent's chat, else Arturo's
        # own calls with no chat at all (never gm's chat read out as if it were the operator's)
        if self.page_thread:
            load, arg = self.server.page_thread_loader, (self.page_thread, self.binding.principal)
        elif self.chat_named:
            load, arg = self.server.thread_loader, (self.chat_agent,)
        else:
            load, arg = self.server.calls_loader, ()
        opening_fut = (asyncio.get_running_loop().run_in_executor(self.server.connect_pool, load, *arg)
                       if threaded else None)
        if self.mode == "offline":
            from .simulator import OfflineProvider
            self.transport = LocalTransport(self)
            self.provider = OfflineProvider(self._offline_push)
        else:
            try:
                self.spend.authorize()
            except spend.SpendStop as e:
                self.emit({"type": "refused", "reason": str(e)})
                return False
            try:
                self.transport = await self._connect()
            except Exception as e:  # noqa: BLE001 — no key, timeout, refused, DNS: the caller is told, nothing leaks
                log.warning(f"native: provider connect failed: {e!r}")
                self.emit({"type": "refused", "reason": f"voice service unreachable: {e}"})
                return False
        self.driver = Driver(self.session, self.transport, _Observer(self))
        self.t0 = time.monotonic()
        self.spend.start()
        opening, skipped = "", None
        if opening_fut is not None:
            try:
                opening = await asyncio.wait_for(asyncio.shield(opening_fut), OPENING_WAIT_S)
                opening = "" if opening is None else opening   # an empty Opening keeps its info (never `or ""`)
            except Exception as e:  # noqa: BLE001 — timeout or a loader error: the call starts without its thread
                skipped = "timeout" if isinstance(e, asyncio.TimeoutError) else "error"
                log.warning(f"native {self.binding.conversation_id}: opening context skipped: {e!r}")
        self.opening_tokens = call_context.thread_context.estimate_tokens(opening)
        # how it was read, so a skipped opening never looks like an empty thread (Claude review of #638)
        self.opening_info = {**call_context.describe(opening), **_OPENING_UNREAD,
                             **(getattr(opening, "info", None) or {}), "skipped": skipped}
        self.driver.send(native_config.session_start_frame(self.toolset.declared_tools(self.binding), context=opening))
        if threaded:
            try:
                self.chat_feed = (self.server.page_feed_factory(self.page_thread, principal=self.binding.principal) if self.page_thread
                                  else self.server.chat_feed_factory(self.chat_agent) if self.chat_named else None)
            except Exception as e:  # noqa: BLE001
                log.warning(f"native {self.binding.conversation_id}: chat feed off: {e!r}")
        if self.provider is not None:
            asyncio.ensure_future(self.provider.start())
        log.info(f"native {self.binding.conversation_id}: START principal={self.binding.principal} "
                 f"device={self.binding.device} tools={len(self.binding.allowed)} "
                 f"claim_gate={'on' if self.gate.enabled else 'off'} chat={self.chat_agent if self.chat_named else '-'} thread={self.page_thread or '-'} "
                 f"opening_tokens={self.opening_tokens} turns={self.opening_info['turns']}")
        self.emit({"type": "state", "mode": self.mode, "conversation_id": self.binding.conversation_id,
                   "principal": self.binding.principal, "caps": {
                       "session_min": self.spend.session_max_s / 60, "session_usd": self.spend.session_max_usd,
                       "day_min": self.server.usage_store.cap_min, "idle_s": self.spend.idle_s}})
        self.emit(self.tools_snapshot())
        return True

    def _on_context(self, d):
        """ios msg_c99d78df: {type: context, page, agent, device, build} from the app (on start and on tab changes).
        Only allowlisted values reach GPT-Live, as app-sourced context, never as Shaw's words or new rules. Off unless
        ARTURO_NATIVE_CONTEXT=1 (review r3 #3: whether GPT-Live voices a null-id instructions.append is unmeasured)."""
        if not CONTEXT_ENABLED or self.mode != "live":
            return
        if d.get("app") is not None:
            self._on_host_context(d)
            return
        try:
            page, agent, device = d.get("page"), d.get("agent"), d.get("device")
            page = page if isinstance(page, str) and page in CONTEXT_PAGES else None
            agent = agent if isinstance(agent, str) and _AGENT_ID_RE.fullmatch(agent) else None
            device = device if isinstance(device, str) and device in CONTEXT_DEVICES else None
        except Exception:  # noqa: BLE001 — a malformed client frame never ends a call
            return
        if page is not None:
            self._context_pending = (page, agent, device)          # coalesced: only the latest is ever sent

    def _on_host_context(self, d):
        """{type: context, app, host: {route, title?, summary?}} from a browser inside another app (the dashboard's
        lib/nativeVoice.ts contextFrame). Bounded by host_page.pick; a bad record is ignored. In the finance tracker
        the first one also starts the finances snapshot, off the event loop (tracker_snapshot: <= its timeout, cached)."""
        try:
            host = host_page.from_frame(d)
        except Exception:  # noqa: BLE001 — a malformed client frame never ends a call
            return
        if host is None:
            return
        self._host_pending = host
        if host["app"] == host_page.TRACKER_APP and self._snapshot is None:
            self._snapshot = "fetching"
            asyncio.ensure_future(self._fetch_snapshot())

    async def _fetch_snapshot(self):
        try:
            text = await asyncio.get_running_loop().run_in_executor(None, tracker_snapshot.get)
        except Exception:  # noqa: BLE001 — no snapshot, the page alone
            text = None
        self._snapshot = text or "done"

    def _flush_host_context(self):
        """From _tick, like _flush_context and sharing its gap: the host page (when it changed) as one app-sourced
        instructions append, then the finances snapshot once. Page text is untrusted data (host_page.native_*)."""
        if self.driver is None or not self.session.started:
            return
        now = time.monotonic()
        if now - self._context_sent_at < CONTEXT_MIN_GAP_S:
            return
        line = None
        if self._host_pending is not None:
            if self._host_pending == self._host or self._host_sends >= HOST_CONTEXT_MAX_SENDS:
                self._host_pending = None
            else:
                line = host_page.native_page_text(self._host_pending)
                self._host, self._host_pending = self._host_pending, None
                self._host_sends += 1
        elif self._snapshot not in (None, "fetching", "done"):
            line, self._snapshot = host_page.native_snapshot_text(self._snapshot), "done"
        if not line:
            return
        try:
            self.driver.send(protocol.instructions_append(line))
        except TransportDown:
            asyncio.ensure_future(self.close("connection lost"))
            return
        self._context_sent_at = now

    def _flush_context(self):
        """From _tick: after session.started, at most one append per CONTEXT_MIN_GAP_S and CONTEXT_MAX_SENDS a call
        (review r3 #2/#4: sent before started it may be lost; alternating tabs must not flood the session)."""
        key = self._context_pending
        if key is None or self.driver is None or not self.session.started:
            return
        if key == self._context:
            self._context_pending = None
            return
        now = time.monotonic()
        if self._context_sends >= CONTEXT_MAX_SENDS or now - self._context_sent_at < CONTEXT_MIN_GAP_S:
            return
        page, agent, device = key
        line = f"Screen context from the app (not Shaw's words): Shaw is on the {page} page"
        line += f', viewing agent "{agent}"' if agent else ""
        line += f", on his {device}." if device else "."
        try:
            self.driver.send(protocol.instructions_append(line))
        except TransportDown:
            asyncio.ensure_future(self.close("connection lost"))
            return
        self._context, self._context_pending = key, None
        self._context_sent_at, self._context_sends = now, self._context_sends + 1

    def _late_note(self, answer):
        """gm's answer to a question the call handed over (ToolRunner, on a tool thread): stamped notes for the next
        _flush_chat_feed, the same path as a chat line typed during the call."""
        try:
            tc = call_context.thread_context
            tz, label = tc.shaw_tz()
            self._feed_notes.extend(tc.live_notes(tc.Line(at=time.time(), who="gm", text=answer, channel="chat"),
                                                  tz, label))
        except Exception as e:  # noqa: BLE001 — the answer still reaches the chat
            log.warning(f"native: gm's late answer could not be queued: {e!r}")

    def _flush_chat_feed(self):
        """From _tick, after session.started: poll the chat every CHAT_POLL_S off the loop; send at most one stamped
        note per tick as a session append (call_context.feed_kind; thinking by default). gm's late answers
        (_late_note) go out the same way, with or without a chat feed."""
        if self.driver is None or not self.session.started:
            return
        now = time.monotonic()
        if self.chat_feed is not None and self._feed_future is None and now >= self._feed_next:
            self._feed_future = self.server.connect_pool.submit(self.chat_feed.poll)
            self._feed_next = now + call_context.CHAT_POLL_S
        if self._feed_future is not None and self._feed_future.done():
            try:
                self._feed_notes.extend(self._feed_future.result() or [])
            except Exception:  # noqa: BLE001 — the next poll tries again
                pass
            self._feed_future = None
        if not self._feed_notes:
            return
        note = self._feed_notes.popleft()
        try:
            self.driver.send(protocol._append(call_context.feed_kind(), note))
        except TransportDown:
            asyncio.ensure_future(self.close("connection lost"))
            return
        self.feed_sent += 1

    def _offline_push(self, frame):
        if self.closed:
            return
        self.transport.inbox.append(json.dumps(frame))
        if self.driver is not None:
            self._pump()

    def _pump(self):
        try:
            self.driver.pump()
        except TransportDown as e:
            asyncio.ensure_future(self.close(f"connection lost: {e}"))

    def _uplink(self, pcm):
        if self.closed:
            return
        if self.t0 is None:
            self.up_pre_start += 1                                 # the phone streams before start: dropped, counted
            return
        if self.up_frames == 0:
            self.up_first_s = round(time.monotonic() - self.t0, 2)  # frames buffered during the connect land at ~0
        self.up_frames += 1
        self.up_bytes += len(pcm)
        from services.arturo import openai_live
        for k in range(0, len(pcm) - len(pcm) % 2, 3200):
            chunk = pcm[k:k + 3200]
            self.session.on_uplink(chunk)
            try:
                self.driver.send(protocol.input_audio_append(base64.b64encode(chunk).decode()))
            except TransportDown:
                asyncio.ensure_future(self.close("connection lost"))
                return
            if openai_live.is_speech(chunk, floor=500):
                self.spend.activity()
                self.up_speech_s += len(chunk) / 32000.0
                self._uplink_speech_s += len(chunk) / 32000.0
                if self._barge_cue is not None:
                    self._cue_speech_s += len(chunk) / 32000.0
                if self.mode == "offline":
                    if self.provider.speaking and self._uplink_speech_s >= BARGE_S:
                        self.provider.interrupt()                 # the simulator really stops (= GPT-Live yielding)
                        if FORCED_BARGE:
                            self.gate.barge()
                        self.emit({"type": "barge", "cut": FORCED_BARGE})
                        self._uplink_speech_s = 0.0
                    continue
                if self.session.assistant_speaking and self._uplink_speech_s >= BARGE_S and self._barge_cue is None:
                    self._barge_cue = time.monotonic()            # GPT-Live decides; _check_barge confirms
                    self._cue_speech_s = 0.0
                    self._uplink_speech_s = 0.0
            else:
                self._uplink_speech_s = 0.0

    def _check_barge(self):
        if self._barge_cue is None:
            return
        now = time.monotonic()
        if now - self._barge_cue >= BARGE_STOP_S and now - self._out_speech_t >= BARGE_STOP_S:
            if FORCED_BARGE:
                self.gate.barge()                             # legacy: drop what the gate still holds
            self.emit({"type": "barge", "cut": FORCED_BARGE})
            self._barge_cue = None
        elif (FORCED_BARGE and now - self._barge_cue >= BARGE_FORCE_S
              and self._cue_speech_s >= BARGE_SUSTAIN_S):
            self._force_muted = True                          # GPT-Live talks on; the caller is really talking
            self.forced_barges += 1
            self.gate.barge()
            self.emit({"type": "barge", "cut": True})
            log.info(f"native {self.binding.conversation_id}: BARGE-FORCED provider still talking "
                     f"{now - self._barge_cue:.2f}s after the cue; muted to the end of the utterance")
            self._barge_cue = None
        elif now - self._barge_cue >= BARGE_GIVEUP_S:
            self._barge_cue = None

    async def _tick(self):
        n = 0
        while not self.closed:
            await asyncio.sleep(0.05)
            self._pump()
            for pcm in self.gate.release(time.monotonic()):
                self.emit(pcm)
            self._check_barge()
            self._check_no_input()
            if await self._check_backend_rejected(time.monotonic()):
                break
            self._flush_context()
            self._flush_chat_feed()
            self._flush_host_context()
            n += 1
            if n % 20 or self.closed:
                continue
            billed = self.session.cost.billed_seconds
            if self.mode == "offline":
                self.emit({"type": "tick", "elapsed_s": round(time.monotonic() - self.t0, 1), "cost": {"spent_usd": 0.0}})
                reason = self.spend.check_clock()
                if reason:
                    await self.close(reason)
                continue
            if n % 600 == 0:                                       # every 30 s: charge the daily cap as we go
                self.spend.checkpoint(billed)
            self.emit({"type": "tick", "elapsed_s": round(time.monotonic() - self.t0, 1),
                       "cost": {"spent_usd": self.spend.usd(billed)}})
            reason = self.spend.check(billed)
            if reason:
                await self.close(reason)

    def _transport_stats(self):
        if self._final_stats is not None:
            return self._final_stats
        f = getattr(self.transport, "stats", None)
        if f is None:
            return {}
        try:
            return dict(f())
        except Exception:  # noqa: BLE001 — evidence only; never break a call for it
            return {}

    def cadence_report(self):
        return {"provider_in": _gap_stats(self._in_gaps), "phone_out": _gap_stats(self._out_gaps),
                "out_gaps_over_150ms_at": self._out_events[:200], "audio_end_at": self._audio_end_at[:200]}

    def uplink_report(self):
        st = self._transport_stats()
        return {"pre_start_frames": self.up_pre_start, "frames": self.up_frames, "bytes": self.up_bytes,
                "audio_s": round(self.up_bytes / 32000.0, 2), "speech_s": round(self.up_speech_s, 2),
                "first_frame_s": self.up_first_s, "provider_started": st.get("started"),
                "appends_sent": st.get("appends_sent"), "pending_s": st.get("pending_s"),
                "pump_alive": st.get("pump_alive")}

    def _check_no_input(self):
        """Once, NO_INPUT_S into a live call: no audio from the client, or audio the provider never got."""
        if self._no_input_checked or self.mode != "live" or self.t0 is None:
            return
        if time.monotonic() - self.t0 < NO_INPUT_S:
            return
        self._no_input_checked = True
        cid = self.binding.conversation_id
        if self.up_frames == 0:
            self.no_input = "no_audio"
            log.warning(f"native {cid}: NO-INPUT no uplink audio after {NO_INPUT_S:.0f} s "
                        f"(pre_start_frames={self.up_pre_start})")
            self.emit({"type": "notice", "code": "no_audio",
                       "text": "I'm not getting any audio from your mic."})
            return
        st = self._transport_stats()
        if st.get("appends_sent") == 0 and st.get("started") is False:
            self.no_input = "provider_not_ready"                    # appends wait for session.started (review r1 #3)
            log.warning(f"native {cid}: OPENAI-NOT-STARTED no session.started after {NO_INPUT_S:.0f} s "
                        f"({self.uplink_report()})")
            self.emit({"type": "notice", "code": "provider_not_ready",
                       "text": "The voice service hasn't started yet."})
            return
        if st.get("appends_sent") == 0:
            self.no_input = "provider_not_hearing"
            log.warning(f"native {cid}: OPENAI-NO-APPENDS {self.up_frames} uplink frames, 0 sent to the provider "
                        f"after {NO_INPUT_S:.0f} s ({self.uplink_report()})")
            self.emit({"type": "notice", "code": "provider_not_hearing",
                       "text": "Your audio reaches the server but not the voice service."})

    async def close(self, reason):
        if self._close_task is None:
            self.closed, self.close_reason = True, reason
            self._close_task = asyncio.create_task(
                self._finalize_close(reason), name=f"native-close-{self.binding.conversation_id[:8]}")
        # Do not propagate cancellation from a transient caller (notably the
        # ticker cancelled by ``run``) into the one teardown that owns report
        # writing and release of the call slot.
        await asyncio.shield(self._close_task)

    async def _finalize_close(self, reason):
        if self.provider is not None:
            self.provider.interrupt()
        if self.driver is not None and self.mode == "live":
            try:
                self.driver.send(protocol.session_close())
            except Exception:  # noqa: BLE001 — an already-dead socket
                pass
            end = time.monotonic() + 5
            while time.monotonic() < end and not self.session.closed and not self.driver.disconnected:
                self._pump()
                await asyncio.sleep(0.05)
        if self.transport is not None and self.mode == "live":    # review r1 #2b: even with no driver yet
            self._final_stats = self._transport_stats()            # before close: pump_alive is about the call
            try:                                                   # close waits up to 5 s: off the loop (r1 #6)
                await asyncio.get_running_loop().run_in_executor(self.server.connect_pool, self.transport.close)
            except Exception:  # noqa: BLE001
                pass
        billed = self.session.cost.billed_seconds
        try:
            if self.mode == "live":
                self.spend.close(billed)
        except Exception as e:  # noqa: BLE001 — never lose the report for a usage-store hiccup
            log.error(f"native {self.binding.conversation_id}: usage record failed: {e!r}")
        rep = self.session.report()
        rep.update(reason=reason, spent_usd=self.spend.usd(billed), claims_blocked=self.gate.blocks,
                   claim_dropped_audio_s=round(self.gate.dropped_s, 2), claim_late=self.gate.late,
                   claim_hold_s=self.gate.hold_s, forced_barges=self.forced_barges,
                   uplink=self.uplink_report(), downlink=self.downlink.report(), no_input=self.no_input,
                   backend_rejected=self.backend_rejected,
                   cadence=self.cadence_report(),
                   thread={"chat": self.chat_agent if self.chat_named else None, "thread_id": self.page_thread,
                           "opening_tokens": self.opening_tokens, "feed_sent": self.feed_sent,
                           **self.opening_info})
        try:
            self.server.write_report(self.binding.conversation_id, rep)
        except Exception as e:  # noqa: BLE001 — release capacity even if disk reporting fails
            log.error(f"native {self.binding.conversation_id}: report write failed: {e!r}")
        finally:
            self.server.calls.pop(self.binding.conversation_id, None)
        log.info(f"native {self.binding.conversation_id}: END reason={reason!r} billed_s={billed} "
                 f"usd={self.spend.usd(billed)} tools={len(rep['tool_calls'])} CLAIM-UNBACKED={len(self.gate.blocks)} "
                 f"CLAIM-GATE-LATE={self.gate.late} hold_s={self.gate.hold_s} forced_barges={self.forced_barges} "
                 f"uplink_frames={rep['uplink']['frames']} uplink_s={rep['uplink']['audio_s']} "
                 f"speech_s={rep['uplink']['speech_s']} pre_start={rep['uplink']['pre_start_frames']} "
                 f"appends_sent={rep['uplink']['appends_sent']} pending_s={rep['uplink']['pending_s']} "
                 f"pump_alive={rep['uplink']['pump_alive']} started={rep['uplink']['provider_started']} "
                 f"first_frame_s={rep['uplink']['first_frame_s']} no_input={self.no_input} "
                 f"in_gap_p50/p90/max={_gap_line(rep['cadence']['provider_in'])} "
                 f"out_gap_p50/p90/max={_gap_line(rep['cadence']['phone_out'])} "
                 f"out_gaps>150ms={rep['cadence']['phone_out']['over_150ms']} "
                 f"opening_tokens={self.opening_tokens} chat_feed_sent={self.feed_sent}")
        self.emit({"type": "stopped", "reason": reason, "report": {
            k: rep[k] for k in ("tool_calls", "duplicate_call_events", "spent_usd", "claims_blocked")}})


# ---- the service -------------------------------------------------------------------------------------------------
class NativeServer:
    def __init__(self, state_dir, executor=None, transport_factory=None, usage_store=None, verify=None, spending=None):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = ToolLedger(self.state_dir)
        self.calls = {}
        from concurrent.futures import ThreadPoolExecutor
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="native-tool")
        self.connect_pool = ThreadPoolExecutor(max_workers=MAX_CALLS + 2, thread_name_prefix="native-connect")
        self.tools = ArturoTools(self.ledger, executor=executor, spoken_fn=self._spoken, spending=spending)
        self.tools.user_fn = self._user_words
        self.tools.thread_fn = self._thread_of
        self.offline_tools = OfflineTools(ToolLedger(self.state_dir / "offline"))
        self.transport_factory = transport_factory or OpenAITransport
        if usage_store is None:
            from services.arturo import voice_usage
            usage_store = voice_usage.UsageStore()
        self.usage_store = usage_store
        self._verify = verify
        self.thread_loader = call_context.opening                   # (agent) -> that agent's chat + Arturo's calls
        self.page_thread_loader = lambda tid, principal: call_context.opening_thread(tid, principal=principal)
        self.calls_loader = call_context.opening_calls              # () -> Arturo's own calls, no chat
        self.page_feed_factory = call_context.ThreadFeed            # (thread id, principal=) -> .poll() -> [notes]
        self.chat_feed_factory = call_context.ChatFeed              # (agent) -> .poll() -> [stamped notes]

    def _spoken(self, conversation_id):
        call = self.calls.get(conversation_id)
        return call.session.spoken_turn if call else ""

    def _thread_of(self, conversation_id):
        """The thread a call's tools tag their messages with (gm_command's subject): the page's own thread, else the
        named agent's chat, else none (the 18:3x call tagged its gm questions "gm chat" on a page-thread call)."""
        call = self.calls.get(conversation_id)
        if call is None:
            return ""
        if getattr(call, "page_thread", None):
            return call.page_thread
        return f"{call.chat_agent} chat" if getattr(call, "chat_named", False) else ""

    def _user_words(self, conversation_id):
        call = self.calls.get(conversation_id)
        return call.session.user_turn_text if call else ""

    def write_report(self, conversation_id, rep):
        d = self.state_dir / "calls"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{conversation_id}.json"
        p.write_text(json.dumps(rep, indent=1, default=str))
        os.chmod(p, 0o600)

    async def ws(self, request):
        if not native_config.enabled():
            return web.Response(status=503, text="native GPT-Live is off (ARTURO_NATIVE_GPTLIVE)")
        principal = verified_principal(request.headers, verify=self._verify)
        if principal is None:
            return web.Response(status=403, text="native calls need the gateway's verified caller")
        device = (request.headers.get("X-Arturo-Device") or "unknown")[:64]
        sock = web.WebSocketResponse(heartbeat=20, max_msg_size=1 << 20)
        await sock.prepare(request)
        call = NativeCall(self, sock, principal, device)
        await call.run()
        return sock

    async def healthz(self, request):
        return web.json_response({"ok": True, "enabled": native_config.enabled(), "calls": len(self.calls),
                                  "claim_gate": native_config.claim_gate_enabled()})

    def app(self):
        a = web.Application()
        a.add_routes([web.get("/native/ws", self.ws), web.get("/native/healthz", self.healthz)])
        return a


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s %(message)s")
    # The spending group loads from the tracker in the background: a tracker that is down never delays or stops this.
    from services.arturo import spending_tools
    srv = NativeServer(ORCHESTRA_DIR / "state" / "arturo" / "native", spending=spending_tools.SpendingTools().arm())
    log.info(f"native GPT-Live on 127.0.0.1:{a.port} (enabled={native_config.enabled()}, "
             f"claim_gate={native_config.claim_gate_enabled()}, backend={native_config.BACKEND_MODEL})")
    web.run_app(srv.app(), host="127.0.0.1", port=a.port, print=None)


if __name__ == "__main__":
    main()
