"""v2/(b) socket-holder relay — the server half of the Watch-Arturo live conversation
(ARCHITECTURE-B.md frozen @ sha256 85c97fdee64a; DEC-1788843712854271 CONSENSUS_REACHED;
gm RED-first authorization msg_ad043c03).

The proxy holds EL's Conversational-AI WebSocket per conversation_id (the SAME agent the
phone uses — brain parity via its custom-LLM callback into :5071) and the watch is a thin
audio relay. This module owns: the per-conversation SocketHolder threads, the bounded
registry (REJECT-NEW cap + idle-TTL), the downlink EventBuffer (SSE + long-poll cursor),
uplink backpressure across EL reconnects (reconnecting event, never a silent drop), the
watch surface registry (relay knowledge beats active-surface inference), server-driven
finalize on relay close, our-side replay across EL reconnects (fresh socket = fresh EL
session — probe-corroborated), and the the operator-directed option-2 live-partials fork.

INERT unless ARTURO_STREAM_RELAY=1; partials additionally behind ARTURO_STREAM_PARTIALS=1.
Nothing here writes CallJournals — the exactly-one journal per conversation is created by
the EL custom-LLM callback path (chat_completions), a locked gm gate criterion.
"""
import base64
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from services.arturo import hume_audio, ptt_stream, voice_vendor
from services.arturo.voice_vendor import _secret

log = logging.getLogger("arturo-stream-relay")

FLAG = "ARTURO_STREAM_RELAY"
PARTIALS_FLAG = "ARTURO_STREAM_PARTIALS"
EL_AGENT_ID = "agent_9001kzmyj4jwe3m8xk2a2s5vn3ac"      # SAME agent as the phone (brain parity)
BYTES_PER_S = 32000                                     # s16le 16k mono
ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", Path.home() / "scripts/agent-orchestra"))
HUME_MAX_SESSION_S = 1800        # Hume EVI hard-caps a chat session; we resume proactively
HUME_RESUME_MARGIN_S = 60        # retire+resume this long BEFORE the cap so audio never hard-drops
IDLE_CLOSE_FLAG = "ARTURO_STREAM_IDLE_CLOSE"   # default OFF; only meaningful under STREAM_RELAY=1
IDLE_CLOSE_S_DEFAULT = 45.0      # DECIDED: protects the operator's 20-30s thinking pauses; env-tunable
VAD_RMS_FLOOR_DEFAULT = 500.0    # speech-vs-room-noise threshold (int16 RMS); tune on-wrist
MUTE_RMS_FLOOR = 30.0            # below this = gated zero-fill / dead mic, resets the soft window
SOFT_REOPEN_S_DEFAULT = 1.5      # I2 deafness guard: sustained above-mute audio reopens


SPEAKING_FLAG = "ARTURO_STREAM_SPEAKING"       # default OFF => downlink byte-identical
# speaking-end debounce; ctor- and env-tunable. 400 flapped on real Hume pacing (ios
# msg_cb3a8769: inter-chunk gaps ~1s => 94 true edges in 6 min); 1500 covers it — a ~1.5s
# trailing 'speaking' is the right trade for an echo gate (over-hold beats flap).
SPEAK_IDLE_MS = int(os.environ.get("ARTURO_SPEAK_IDLE_MS", "1500"))
# Env-overridable (gm msg_913187bd): tunable mid-incident + bite-provable without code edits.
FRAMED_STORM_STREAK = int(os.environ.get("ARTURO_FRAMED_STORM_STREAK", "3"))
                                               # consecutive in-grace framed deaths => cooldown
FRAMELESS_GRACE_S = float(os.environ.get("ARTURO_FRAMELESS_GRACE_S", "2.0"))
                                               # a socket dying frameless inside this window
                                               # counts as a CONNECT FAILURE (storm-breaker)
HUME_FINAL_DUP_WINDOW_S = 6.0    # window in which a repeat/superset user-final is ONE utterance
UPLINK_TAP_FLAG = "ARTURO_UPLINK_TAP"          # diagnostic: raw uplink capture, default OFF
UPLINK_TAP_MAX_BYTES = 10 * 1024 * 1024        # per-conversation cap (~5.5 min of 16k s16le)


def _uplink_tap(conversation_id, pcm):
    """Diagnostic tap (ios msg_58d8b8af): append the raw uplink PCM exactly as it arrived —
    vendor-independent, BEFORE any gating — to state/uplink-tap/<cid>.pcm so a wrist-audio
    problem (silence holes from a client gate flap vs low level) is diagnosable server-side
    and the identical bytes are replayable. NEVER raises: a tap failure is invisible."""
    try:
        safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(conversation_id))[:120]
        d = ORCHESTRA_DIR / "state" / "uplink-tap"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{safe}.pcm"
        if p.exists() and p.stat().st_size >= UPLINK_TAP_MAX_BYTES:
            return
        with open(p, "ab") as f:
            f.write(pcm)
    except Exception:
        pass


def idle_close_enabled():
    return os.environ.get(IDLE_CLOSE_FLAG, "") == "1"


def speaking_enabled():
    return os.environ.get(SPEAKING_FLAG, "") == "1"


def agent_text_enabled():
    # Hume reply-text streaming to the watch (ios msg_0864f940, contract (a) msg_57c21e6c).
    # Default OFF: pre-218 clients would STACK rows on the partial emissions, so the flag
    # arms only at the coordinated respawn after ios's watch>=218-on-wrist word + gm gate.
    return os.environ.get("ARTURO_STREAM_AGENT_TEXT", "") == "1"


def _rms(pcm):
    """int16 RMS of an uplink chunk (server-VAD reopen gate)."""
    import numpy as np
    n = len(pcm) // 2 * 2
    if n == 0:
        return 0.0
    x = np.frombuffer(pcm[:n], "<i2").astype(np.float32)
    return float(np.sqrt(np.mean(x * x)))


def enabled():
    return os.environ.get(FLAG, "") == "1"


def partials_enabled():
    return os.environ.get(PARTIALS_FLAG, "") == "1"


def _el_api_key():
    try:
        for line in open(ORCHESTRA_DIR / ".env.secrets"):
            if line.startswith("ELEVENLABS_API_KEY="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return os.environ.get("ELEVENLABS_API_KEY", "")


def _el_sock(ws):
    """Wrap a websocket-client connection, SURFACING the close code+reason in the raised
    exception (ios msg_2c5d8d9c: EL was closing 3000 [quota_exceeded] and the bare
    ConnectionClosed hid it — the storm's cause was invisible until a manual probe)."""
    import websocket

    class _Sock:
        def send(self, payload):
            ws.send(payload)

        def recv(self):
            op, data = ws.recv_data(control_frame=False)
            if op == websocket.ABNF.OPCODE_CLOSE:
                payload = getattr(data, "data", data) or b""
                code = int.from_bytes(payload[:2], "big") if len(payload) >= 2 else 0
                reason = payload[2:].decode("utf-8", "replace") if len(payload) > 2 else ""
                try:
                    ws.close()
                except Exception:
                    pass
                raise RuntimeError(f"el socket closed: code {code} {reason}".strip())
            return data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else data

        def close(self):
            try:
                ws.close()
            except Exception:
                pass
    return _Sock()


def _default_socket_factory(conversation_id):
    """Real EL Conv-AI socket (probe-verified wire format: {'user_audio_chunk': b64} up;
    typed events down; ping requires pong)."""
    import websocket  # websocket-client: sync, thread-friendly
    ws = websocket.create_connection(
        f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={EL_AGENT_ID}",
        header={"xi-api-key": _el_api_key()}, timeout=30)
    ws.settimeout(None)   # same latent read-timeout class as hume (EL's ~20s pings mask it)
    return _el_sock(ws)


def hume_socket_factory(conversation_id, resumed_chat_group_id=None):
    """Real Hume EVI socket (spec §4.2). verbose_transcription=true is what makes Hume emit
    interim user_message frames (our native-partials replacement for the Scribe fork).
    HUME_CONFIG_VERSION comes from .env.secrets — pinned, never hardcoded."""
    import urllib.parse
    import websocket
    params = {
        "config_id": _secret("HUME_CONFIG_ID"),
        "config_version": _secret("HUME_CONFIG_VERSION"),
        "verbose_transcription": "true",
    }
    if resumed_chat_group_id:
        params["resumed_chat_group_id"] = resumed_chat_group_id
    ws = websocket.create_connection(
        "wss://api.hume.ai/v0/evi/chat?" + urllib.parse.urlencode(params),
        header={"X-Hume-Api-Key": _secret("HUME_API_KEY")}, timeout=30)
    # timeout=30 is the HANDSHAKE guard ONLY — it doubles as the socket READ timeout, and
    # Hume sends NO frames while the user is quiet (no pings), so a blocking recv() raised
    # WebSocketTimeoutException every 30s and the reader retired into a fresh chat (the
    # 33.6s/30.1s chat lifetimes on the operator's call — ios msg_fd94e988). Clear it after connect.
    ws.settimeout(None)
    # session_settings MUST be the FIRST uplink frame (on-wrist RED msg_b6ec3388, proven 4
    # ways): without the audio declaration Hume never treats our 16k PCM as speech (deaf
    # after greeting); custom_session_id keys the CLM callback to OUR cid (T8 attribution);
    # language_model_api_key is the CLM bearer (401 without it).
    settings = {
        "type": "session_settings",
        "audio": {"encoding": "linear16", "sample_rate": 16000, "channels": 1},
        "custom_session_id": conversation_id,
        "language_model_api_key": _secret("CUSTOM_LLM_BEARER"),
    }
    # the operator's voice picker (contract msg_f5b57c9d): the runtime voice pref rides
    # session_settings.voice_id per connect — no config re-pin, no restart per pick.
    # Absent pref => the pinned config's default voice governs.
    from services.arturo import voice_choice as _voice_choice
    _vid = _voice_choice.get_voice("hume")
    if _vid:
        settings["voice_id"] = _vid
    ws.send(json.dumps(settings))

    class _Sock:
        def send(self, payload):
            ws.send(payload)
        def recv(self):
            return ws.recv()
        def close(self):
            try:
                ws.close()
            except Exception:
                pass
    return _Sock()


def _encode_uplink(vendor, pcm):
    """One place that knows each vendor's uplink audio frame."""
    b64 = base64.b64encode(pcm).decode()
    if vendor == "hume":
        return json.dumps({"type": "audio_input", "data": b64})
    return json.dumps({"user_audio_chunk": b64})


def _default_scribe(pcm):
    """Option-2 partials transcription: EL scribe_v1 over the growing turn buffer (raw PCM is
    wrapped in a minimal WAV header so scribe detects the format)."""
    import struct
    import importlib.util
    hdr = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
           struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) +
           b"data" + struct.pack("<I", len(pcm)))
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_scribe", str(Path(__file__).parent / "arturo-proxy.py"))
    # NOTE: we deliberately do NOT import the proxy here (heavy). The scribe HTTP call is
    # inlined with the same shape as _ptt_stt_elevenlabs to stay import-light.
    import uuid
    import urllib.request
    import urllib.error
    key = _el_api_key()
    boundary = "----arturo-partial-" + uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"model_id\"\r\n\r\nscribe_v1\r\n".encode()
            + (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"turn.wav\"\r\n"
               f"Content-Type: audio/wav\r\n\r\n").encode() + hdr + pcm + b"\r\n"
            + f"--{boundary}--\r\n".encode())
    req = urllib.request.Request("https://api.elevenlabs.io/v1/speech-to-text", data=body,
                                 headers={"xi-api-key": key,
                                          "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return (json.loads(r.read()).get("text") or "").strip()


class _Holder:
    """One live conversation: EL socket + reader thread + reconnect + partials fork."""

    def __init__(self, conversation_id, manager, vendor="elevenlabs"):
        self.cid = conversation_id
        self.m = manager
        self.vendor = vendor
        self.sock = None
        self.gate = ptt_stream.UplinkGate()
        self.turn_pcm = ptt_stream.TurnPcmBuffer()
        self.partials = None
        # The Scribe partials fork is EL-only: Hume ships native interim user_messages
        # (verbose_transcription), so the O(T^2) re-STT fork stays forced OFF for hume.
        if manager.partials_enabled and vendor == "elevenlabs":
            self.partials = ptt_stream.PartialsEngine(
                scribe_fn=manager.scribe_fn,
                emit_fn=lambda e: manager.buffer.put(self.cid, e),
                cadence_s=manager.partials_cadence_s, bytes_per_s=BYTES_PER_S)
        self._lock = threading.Lock()
        self._closed = False
        self._reconnecting = False
        self._turn_no = 1         # advances per vendor final; matches the partials engine's counter
        self._last_connect_fail = 0.0   # feed-path handshake cooldown (rate-limit self-defense)
        self._connected_at = 0.0  # storm-breaker: when the current socket connected
        self._got_frame = False   # storm-breaker: did the current socket deliver ANY frame
        self._fast_death_streak = 0  # consecutive socket_down deaths inside the grace window
        self._frameless_fail = False    # last death was open-then-frameless: cooldown gates
                                        # the RECONNECT path too (else 2-3 connects/s storm)
        self._credit_alerted = False    # one visible credit beat per outage, not per retry
        self._agent_output_seen = False  # hume: gates barge_in (greeting-clip guard)
        self._last_final_norm = ""       # hume: dedup window for repeat/superset finals
        self._last_final_ts = 0.0
        self._rev = 0             # hume: interim revision counter within the current turn
        self._agent_buf = []      # hume: assistant_message segments awaiting assistant_end
        self._agent_turn = 0      # hume: assistant-turn counter for streamed agent_response
        self.ar_partials = 0      # by-effect proof (ios msg_3a5b4d4b): counted at end()
        self.ar_finals = 0
        self.chat_group_id = None  # hume: learned from chat_metadata; keys proactive resume
        self._gen = 0             # bumps on every socket retirement; stale timers check it
        self._timers = []         # armed threading.Timers; cancelled on retire/close
        self._idle_closed = False  # last retirement was reconnect=False (user-driven reopen only)
        self._last_activity = time.time()   # C2: user final / agent_response / every audio chunk
        self._soft_s = 0.0        # I2: accumulated sustained above-mute (sub-speech) seconds
        # agent_speaking state machine (spec @642abdd9): its OWN lock+gen, separate from the
        # socket-lifecycle _gen — a socket retirement must not disturb a speaking span and
        # vice versa. Timer.cancel() is a no-op once a callback began, so the GEN TOKEN is
        # the real guard: every state change bumps _speak_gen; a stale fired-while-blocked
        # timer sees its captured gen != current and no-ops.
        self._speaking = False
        self._speak_timer = None
        self._speak_gen = 0
        self._speak_lock = threading.Lock()

    # -- lifecycle --
    def ensure_socket(self, from_reconnect=False):
        with self._lock:
            if self.sock is not None or self._closed:
                return self.sock is not None
            # Cooldown on the feed path: a holder whose last handshake failed must not
            # re-attempt on every uplink chunk (that pins EL's per-key 429 window open). The
            # _reconnect loop has its own exponential backoff and bypasses this — EXCEPT after
            # an open-then-FRAMELESS death (ios msg_59d6dcd5: EL sockets opened then died
            # before any frame; that "successful" connect bypassed the cooldown entirely and
            # the reconnect chain stormed 2-3/s). A frameless death gates BOTH paths.
            if (self._last_connect_fail
                    and time.time() - self._last_connect_fail < self.m.connect_cooldown_s
                    and (not from_reconnect or self._frameless_fail)):
                return False
        try:
            if self.vendor == "hume":
                s = self.m.factories["hume"](self.cid, resumed_chat_group_id=self.chat_group_id)
            else:
                s = self.m.socket_factory(self.cid)
        except Exception as e:
            with self._lock:
                self._last_connect_fail = time.time()
            log.error(f"relay {self.cid}: socket connect failed: {e}")
            return False
        with self._lock:
            if self._closed:
                try:
                    s.close()
                except Exception:
                    pass
                return False
            self.sock = s
            self._last_connect_fail = 0.0        # clean connect resets the cooldown
            self._idle_closed = False
            self._connected_at = time.time()     # storm-breaker bookkeeping per socket
            self._got_frame = False
            self._agent_output_seen = False      # fresh session: greeting-clip guard re-arms
        threading.Thread(target=self._reader, args=(s,), daemon=True,
                         name=f"relay-reader-{self.cid[:8]}").start()
        # B2 (deep-review blocker): the resume timer is armed OUTSIDE the holder lock — arming
        # under it deadlocks the very first hume connect (_arm_timer takes the lock itself).
        if self.vendor == "hume" and self.m.hume_session_s:
            self._arm_timer(max(self.m.hume_session_s - HUME_RESUME_MARGIN_S, 0.05),
                            self._proactive_resume)
        if self.m.idle_close:
            self._touch_activity()
            self._arm_timer(self.m.idle_close_s, self._idle_watch)
        return True

    def _touch_activity(self):
        self._last_activity = time.time()

    def _idle_watch(self):
        """Gen-guarded idle watcher (spec C3 via _arm_timer/_fire): re-arms while activity is
        fresh; on a true idle fire, retires WITHOUT reconnect via the single lifecycle owner —
        the conversation (holder/surface/registry slot) stays alive, only the socket cycles."""
        with self._lock:
            if self._closed or self.sock is None:
                return
            remaining = self.m.idle_close_s - (time.time() - self._last_activity)
        if remaining > 0:
            self._arm_timer(remaining, self._idle_watch)
            return
        self.m.prune_el_ids(self.cid)     # I3: this cycle's EL ids are historical after the close
        self._retire_socket("idle", reconnect=False)
        self.m.buffer.put(self.cid, {"type": "el_idle_closed"})   # low-key, NOT 'reconnecting'

    # -- timers (all gen-guarded; Timer.cancel() is a no-op once a callback began, so the
    #    generation check in _fire is the real guard, cancel() is just an optimization) --
    def _arm_timer(self, delay_s, fn):
        with self._lock:
            if self._closed:
                return
            gen = self._gen
            t = threading.Timer(delay_s, self._fire, args=(gen, fn))
            t.daemon = True
            self._timers.append(t)
        t.start()

    def _fire(self, gen, fn):
        with self._lock:
            if gen != self._gen or self._closed:
                return
        fn()

    def _cancel_timers(self):
        with self._lock:
            timers, self._timers = self._timers, []
        for t in timers:
            try:
                t.cancel()
            except Exception:
                pass

    def _retire_socket(self, reason, *, reconnect, detail=None):
        """THE single socket-lifecycle owner: reconnect-on-death, idle-close, agent_speaking,
        proactive resume and connect-cooldown ALL funnel here. Nothing else may touch self.sock.
        reconnect=False retires WITHOUT respawn — only a fresh uplink chunk reopens."""
        self._cancel_timers()
        self.m.mark_needs_replay(self.cid)    # fresh vendor session owed our-side replay from NOW
        spawn = False
        with self._lock:
            self._gen += 1                    # invalidate every in-flight timer callback
            s, self.sock = self.sock, None
            self._idle_closed = not reconnect
            # Storm-breaker: an open-then-frameless DEATH within the grace window = a connect
            # failure in disguise — trip the cooldown and gate the reconnect chain too. ONLY
            # for the socket_down failure path: deliberate retirements (idle-close, proactive
            # resume, agent_speaking) legitimately retire young frameless sockets and their
            # reopens must never be cooldown-blocked.
            credit_beat = False
            fast_death = (reason == "socket_down" and s is not None
                          and self._connected_at
                          and time.time() - self._connected_at < FRAMELESS_GRACE_S)
            if fast_death:
                self._fast_death_streak += 1
                # Trip on the FIRST frameless death (a connect failure in disguise), or on a
                # STREAK of framed instant deaths (ios msg_33a85910: Hume sent an I0100 error
                # FRAME before dropping each socket, so _got_frame exempted every death and
                # the reconnect chain looped ~1/s — frames don't make a 1s-lifetime loop healthy).
                if not self._got_frame or self._fast_death_streak >= FRAMED_STORM_STREAK:
                    self._last_connect_fail = time.time()
                    self._frameless_fail = True
                    # credit-class close (EL 3000 [quota_exceeded]): surface ONE visible beat per
                    # outage instead of a silent bounded storm (ios msg_2c5d8d9c).
                    if (detail and ("quota_exceeded" in detail or "code 3000" in detail)
                            and not self._credit_alerted):
                        self._credit_alerted = True
                        credit_beat = True
            elif reason == "socket_down":
                self._fast_death_streak = 0      # lived past the grace window: not a storm
            if reconnect and not self._closed and not self._reconnecting:
                self._reconnecting = True
                spawn = True
        if credit_beat:
            _msg = f"{self.vendor}: out of credits — add credits or switch vendor"
            self.m.record_vendor_refusal(self.vendor, _msg)
            self.m.buffer.put(self.cid, {"type": "vendor_unavailable", "message": _msg})
        if self.vendor == "hume":
            self._flush_agent_buf()           # never strand a half-coalesced assistant turn
        if s:
            try:
                s.close()
            except Exception:
                pass
        log.info(f"relay {self.cid}: socket retired ({reason}, reconnect={reconnect}"
                 + (f", cause={detail}" if detail else "") + ")")
        if spawn:
            threading.Thread(target=self._reconnect, daemon=True,
                             name=f"relay-reconn-{self.cid[:8]}").start()

    def _proactive_resume(self):
        # hume session-cap resume: retire+reconnect BEFORE Hume hard-drops the chat; the fresh
        # socket resumes the same chat_group_id (context carries over server-side at Hume).
        self._retire_socket("session_cap_resume", reconnect=True)

    def _flush_agent_buf(self):
        with self._lock:
            segs, self._agent_buf = self._agent_buf, []
            turn = self._agent_turn
        text = " ".join(x.strip() for x in segs if x and x.strip()).strip()
        if text:
            self.m.replay_log(self.cid).add("agent", text)
            if self.m.agent_text_enabled:
                # contract (a): the flush IS the turn's partial:false final (assistant_end,
                # forwarded interruption, and retire-flush all funnel here)
                self.ar_finals += 1
                self.m.buffer.put(self.cid, {"type": "agent_response", "text": text,
                                             "agent_turn": turn, "partial": False})
            else:
                self.m.buffer.put(self.cid, {"type": "agent_response", "text": text})

    def close(self):
        self._cancel_timers()
        # speaking teardown: NO speaking:false on close (finalize owns end-of-call; the
        # client treats end/410 as implicitly not-speaking) — but bump the gen so a
        # fired-while-blocked stale timer can never emit after close.
        with self._speak_lock:
            self._speaking = False
            self._speak_gen += 1
            t, self._speak_timer = self._speak_timer, None
        if t:
            try:
                t.cancel()
            except Exception:
                pass
        with self._lock:
            self._gen += 1
            self._closed = True
            s, self.sock = self.sock, None
        if s:
            try:
                s.close()
            except Exception:
                pass

    # -- uplink --
    def feed(self, pcm):
        if self.m.idle_close:
            if self._idle_closed:
                # C1: while idle-closed, ONLY speech-level energy (or I2 sustained soft speech)
                # reopens — mute-silence / room noise stays closed, silently (no beat).
                if not self._reopen_gate(pcm):
                    return
                with self._lock:
                    self._idle_closed = False
                self._soft_s = 0.0
                self._touch_activity()
                self.m.buffer.put(self.cid, {"type": "el_idle_resumed"})   # SILENT-class beat
            elif _rms(pcm) >= self.m.vad_rms_floor:
                self._touch_activity()     # speech uplink keeps the idle timer fresh
        self.turn_pcm.append(pcm)
        if self.partials and not self._idle_closed:   # I1: no Scribe billing on idle audio
            self.partials.feed(pcm)
        if not self.ensure_socket():
            self._buffer_outage(pcm)
            return
        try:
            self.sock.send(_encode_uplink(self.vendor, pcm))
        except Exception:
            self._on_socket_down()
            self._buffer_outage(pcm)

    # -- agent_speaking boundary (spec @642abdd9; only when manager.speaking_enabled) --
    def _speak_event(self, speaking):
        self.m.buffer.put(self.cid, {"type": "agent_speaking", "speaking": speaking,
                                     "epoch_ms": int(time.time() * 1000)})

    def _speak_on_audio(self):
        """audio chunk: open the span on the first chunk; (re)arm the idle timer on every
        chunk, bumping the gen so ONLY the latest-armed timer can ever close the span."""
        with self._speak_lock:
            if not self._speaking:
                self._speaking = True
                self._speak_gen += 1
                self._speak_event(True)             # at most once per span (edge only)
            self._speak_gen += 1
            g = self._speak_gen
            old = self._speak_timer
            t = threading.Timer(self.m.speak_idle_ms / 1000.0, self._speak_idle_fire, args=(g,))
            t.daemon = True
            self._speak_timer = t
        if old:
            try:
                old.cancel()                        # best-effort; the gen token is the guard
            except Exception:
                pass
        t.start()

    def _speak_idle_fire(self, g):
        with self._speak_lock:
            if g != self._speak_gen or not self._speaking:
                return                              # stale/superseded timer: no-op
            self._speaking = False
            self._speak_gen += 1
            self._speak_event(False)

    def _speak_off(self):
        """interruption / user-final / turn boundary: close the span NOW (if open)."""
        with self._speak_lock:
            t, self._speak_timer = self._speak_timer, None
            self._speak_gen += 1                    # invalidates any pending/blocked timer
            was = self._speaking
            self._speaking = False
            if was:
                self._speak_event(False)
        if t:
            try:
                t.cancel()
            except Exception:
                pass

    def _reopen_gate(self, pcm):
        """Server-VAD reopen decision while idle-closed (spec: speech-vs-noise, NOT non-zero)."""
        rms = _rms(pcm)
        if rms >= self.m.vad_rms_floor:
            return True                  # speech (a tap's resumed feed lands here too)
        if rms > MUTE_RMS_FLOOR:
            self._soft_s += len(pcm) / BYTES_PER_S
            return self._soft_s >= self.m.soft_reopen_s   # I2: soft speech can't deafen
        self._soft_s = 0.0               # gated zero-fill resets the sustained window
        return False

    def _buffer_outage(self, pcm):
        if self.gate.buffer(pcm):        # once per outage: surface it, never silently drop
            self.m.buffer.put(self.cid, {"type": "reconnecting"})

    def _on_socket_down(self, detail=None):
        # the vendor session died under us: retire-with-reconnect via the ONE lifecycle owner
        # (our-side replay is owed from THIS moment — _retire_socket marks it).
        self._retire_socket("socket_down", reconnect=True, detail=detail)

    def _reconnect(self):
        try:
            for attempt in range(5):
                if self.ensure_socket(from_reconnect=True):
                    self.m.mark_needs_replay(self.cid)     # fresh EL session: our-side replay owed
                    for chunk in self.gate.drain():
                        try:
                            self.sock.send(_encode_uplink(self.vendor, chunk))
                        except Exception:
                            break
                    self.m.buffer.put(self.cid, {"type": "reconnected"})
                    return
                time.sleep(min(0.5 * (2 ** attempt), 4))
            log.error(f"relay {self.cid}: reconnect gave up")
        finally:
            with self._lock:
                self._reconnecting = False

    # -- downlink --
    def _reader(self, sock):
        while True:
            try:
                raw = sock.recv()
                if not self._got_frame:
                    self._got_frame = True           # healthy session: not a frameless death
                    self._frameless_fail = False
                    self._credit_alerted = False     # outage over: next outage may beat again
                    self.m.clear_vendor_refusal(self.vendor)   # vendor proved alive
            except Exception as e:
                if type(e).__name__ == "WebSocketTimeoutException":
                    continue    # read-timeout = vendor silence, NEVER death (msg_fd94e988)
                with self._lock:
                    dead_current = self.sock is sock
                if dead_current and not self._closed:
                    # NEVER retire silently — the operator's first hume call had 2 causeless
                    # socket_downs (ios msg_137baa61); the cause travels to the retire path
                    # so a credit-class close can surface visibly.
                    log.warning(f"relay {self.cid}: reader recv failed: {e!r}")
                    self._on_socket_down(detail=repr(e))
                return
            try:
                d = json.loads(raw)
            except Exception:
                continue
            try:
                self._dispatch(d, sock)
            except Exception as e:
                # a dispatch bug must never silently kill the reader — that leaves a
                # deaf-alive socket with NO socket_down at all. Log and keep reading.
                log.error(f"relay {self.cid}: dispatch failed on {d.get('type')!r}: {e!r}")

    def _dispatch(self, d, sock):
        if self.vendor == "hume":
            self._dispatch_hume(d, sock)
        else:
            self._dispatch_el(d, sock)

    def _dispatch_hume(self, d, sock):
        """Hume EVI downlink (spec §4.2). Tools NEVER run here — the CLM path owns them; a
        socket-side tool_call gets a visible tool_error. Hume needs no ping/pong."""
        t = d.get("type", "")
        self.m.registry.touch(self.cid)
        if t == "ping":
            return
        if t == "chat_metadata":
            gid = d.get("chat_group_id") or ""
            if gid:
                self.chat_group_id = gid          # keys resumed_chat_group_id on proactive resume
            self.m.buffer.put(self.cid, {"type": t})
            return
        if t == "user_message":
            if d.get("from_text"):
                return                            # our own injected text echoed back — not speech
            text = ((d.get("message") or {}).get("content") or "").strip()
            if d.get("interim"):
                with self._lock:
                    self._rev += 1
                    rev, turn = self._rev, self._turn_no
                self.m.buffer.put(self.cid, {"type": "user_partial", "text": text,
                                             "turn": turn, "revision": rev})
                return
            # Hume emits MULTIPLE interim:false finals per utterance as growing supersets
            # (ios msg_7293955c): within the window, a duplicate/subset re-final is absorbed
            # (no new turn) and a superset REPLACES the same turn (client re-renders it).
            if self.m.speaking_enabled:
                self._speak_off()                 # turn boundary (mirrors EL user_transcript)
            norm = " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())
            now = time.time()
            prev, prev_ts = self._last_final_norm, self._last_final_ts
            in_window = prev and (now - prev_ts) < HUME_FINAL_DUP_WINDOW_S
            self._touch_activity()       # C2
            if in_window and norm and (norm == prev or prev.startswith(norm + " ") or prev == norm):
                self._last_final_ts = now
                return                   # duplicate/subset re-final: absorbed
            if in_window and norm.startswith(prev + " "):
                self._last_final_norm, self._last_final_ts = norm, now
                if text:
                    self.m.replay_log(self.cid).add("user", text)
                with self._lock:
                    turn = max(self._turn_no - 1, 1)     # SAME turn: replacement, not a new one
                self.m.buffer.put(self.cid, {"type": "user_transcript", "text": text, "turn": turn})
                return
            self._last_final_norm, self._last_final_ts = norm, now
            if text:
                self.m.replay_log(self.cid).add("user", text)
            with self._lock:
                turn = self._turn_no
                self._turn_no += 1
                self._rev = 0
            self.turn_pcm.end_turn()
            self.m.buffer.put(self.cid, {"type": "user_transcript", "text": text, "turn": turn})
            return
        if t == "assistant_message":
            if d.get("from_text"):
                return
            # NOTE: text does NOT unlock barge_in — assistant_message precedes Hume's
            # spurious silence-interruption on real calls (v1 keyed on it and barge_in still
            # hit the wire before the greeting, ios msg_b8f46733). Audio-forwarded only.
            with self._lock:
                if not self._agent_buf:
                    self._agent_turn += 1     # a fresh assistant turn opens on its 1st segment
                self._agent_buf.append(((d.get("message") or {}).get("content") or ""))
                segs = list(self._agent_buf)
                turn = self._agent_turn
            if self.m.agent_text_enabled:
                # ios msg_0864f940: stream the CUMULATIVE turn text per segment so the watch
                # renders Arturo's reply while he speaks; agent_turn lets the client replace
                # the row in place. replay_log/journal stay end-only (_flush_agent_buf).
                text = " ".join(x.strip() for x in segs if x and x.strip()).strip()
                if text:
                    self.ar_partials += 1
                    self.m.buffer.put(self.cid, {"type": "agent_response", "text": text,
                                                 "agent_turn": turn, "partial": True})
            return
        if t == "assistant_end":
            self._touch_activity()                # C2
            self._flush_agent_buf()               # coalesce the segments into ONE agent_response
            self.m.buffer.put(self.cid, {"type": "assistant_end"})
            return
        if t == "audio_output":
            self._touch_activity()                # C2: never idle-close mid-agent-speech
            self._agent_output_seen = True
            if self.m.speaking_enabled:
                self._speak_on_audio()            # wave-5: hume path speaks too (echo gate)
            try:
                pcm = hume_audio.wav_to_pcm16k(base64.b64decode(d.get("data") or ""))
            except Exception:
                pcm = b""
            if pcm:
                self.m.buffer.put(self.cid, {"type": "audio",
                                             "audio": base64.b64encode(pcm).decode()})
            return
        if t == "user_interruption":
            # Greeting-clip guard (ios msg_59d6dcd5): Hume fires user_interruption on the
            # first silence chunks WHILE generating the greeting; forwarding barge_in then
            # makes the watch flush playback and clip it. Only forward once THIS socket has
            # actually produced agent output — a real mid-speech interruption always has.
            if not self._agent_output_seen:
                return
            if self.m.speaking_enabled:
                self._speak_off()                 # speaking:false lands BEFORE barge_in
            if self.m.agent_text_enabled:
                self._flush_agent_buf()           # contract (a): final lands BEFORE barge_in
            self.m.buffer.put(self.cid, {"type": "barge_in"})
            return
        if t == "tool_call":
            try:
                sock.send(json.dumps({"type": "tool_error",
                                      "tool_call_id": d.get("tool_call_id"),
                                      "error": "tools_run_on_clm_path",
                                      "content": "Tools are handled by the custom language model path."}))
            except Exception:
                pass
            return
        if t == "error":
            # ios msg_bf132099: Hume E0300 zero_credits arrived as a bare passthrough the
            # watch ignores — greeting then silence. Credit/auth-class errors get the SAME
            # one-shot visible beat as an EL 3000 close + the claim-time refusal cache.
            code = str(d.get("code") or "")
            slug = str(d.get("slug") or "")
            msg = str(d.get("message") or "")
            blob = f"{code} {slug} {msg}".lower()
            log.warning(f"relay {self.cid}: hume error event: {code} {slug} {msg[:120]}")
            if "credit" in blob or code == "E0300":
                _msg = ("hume: out of credits — add credits at platform.hume.ai/billing "
                        "or switch vendor")
                self.m.record_vendor_refusal(self.vendor, _msg)
                if not self._credit_alerted:
                    self._credit_alerted = True
                    self.m.buffer.put(self.cid, {"type": "vendor_unavailable", "message": _msg})
                return
            self.m.buffer.put(self.cid, {"type": t})
            return
        self.m.buffer.put(self.cid, {"type": t or "unknown"})

    def _dispatch_el(self, d, sock):
        t = d.get("type", "")
        self.m.registry.touch(self.cid)
        if t == "ping":
            eid = (d.get("ping_event") or {}).get("event_id")
            try:
                sock.send(json.dumps({"type": "pong", "event_id": eid}))
            except Exception:
                pass
            return
        if t == "user_transcript":
            self._touch_activity()       # C2
            if self.m.speaking_enabled:
                self._speak_off()        # turn boundary: agent's turn is over
            text = ((d.get("user_transcription_event") or {}).get("user_transcript") or "").strip()
            if text:
                self.m.replay_log(self.cid).add("user", text)
            with self._lock:
                turn_no = self._turn_no
                self._turn_no += 1
            if self.partials:
                self.partials.turn_final()      # EL final supersedes + closes the partial sequence
                self.partials.turn_start()      # engine counter advances in lockstep with _turn_no
            self.turn_pcm.end_turn()
            # `turn` matches the turn's user_partial events — the client swaps partial->final
            # keyed on it and drops any partial at/before the last committed final.
            self.m.buffer.put(self.cid, {"type": "user_transcript", "text": text, "turn": turn_no})
            return
        if t == "agent_response":
            self._touch_activity()       # C2
            text = ((d.get("agent_response_event") or {}).get("agent_response") or "").strip()
            if text:
                self.m.replay_log(self.cid).add("agent", text)
            self.m.buffer.put(self.cid, {"type": "agent_response", "text": text})
            return
        if t == "interruption":
            if self.m.speaking_enabled:
                self._speak_off()        # speaking:false lands BEFORE barge_in
            # barge-in: tell the watch to flush its playback queue
            self.m.buffer.put(self.cid, {"type": "barge_in"})
            return
        if t == "audio":
            # C2 REQUIRED: TTS audio streams for seconds after the one agent_response — without
            # this touch, idle-close could fire MID-AGENT-SPEECH and cut Arturo off.
            self._touch_activity()
            if self.m.speaking_enabled:
                self._speak_on_audio()
            audio_b64 = ((d.get("audio_event") or {}).get("audio_base_64") or "")
            self.m.buffer.put(self.cid, {"type": "audio", "audio": audio_b64})
            return
        if t == "conversation_initiation_metadata":
            # criterion #5 (EL<->our conv_id mapping): EL's socket-path custom-LLM callbacks
            # carry EL's own conversation id (or NOTHING — smoke-proven msg_2c36c4d2), never
            # ours. Learn EL's id here so the journal/replay seams can resolve back to OUR id.
            el_id = ((d.get("conversation_initiation_metadata_event") or {}).get("conversation_id") or "")
            if el_id:
                self.m.map_el_id(el_id, self.cid)
            self.m.buffer.put(self.cid, {"type": t})
            return
        # anything else: pass through typed (agent_response_correction, ...)
        self.m.buffer.put(self.cid, {"type": t or "unknown"})


# --- surface stamping (DEC-1789341362142252, X-Surface; UA sub-choice superseded, gm msg_b40c6ccf) ---
# The phone sends `X-Surface: phone` on every relay request (Sources/RelayClient/RelayTransport.swift:108,
# set by iOS RelayVoiceEngine.swift:59); the watch sends no header. The gateway forwards X-Surface to
# the /ptt/stream/audio endpoint, which passes it to feed_audio(surface_device=). Any missing/unknown
# value stamps `watch` (the pre-change default). Attribution is set ONCE at holder creation.
_KNOWN_SURFACES = ("phone", "watch")


def normalize_surface(val):
    """Map a raw X-Surface header value to a known device; missing/unknown -> 'watch'."""
    return val if val in _KNOWN_SURFACES else "watch"


def journal_surface(relay_surface, active_surface_fallback):
    """Build the journal's surface dict. A relay call (relay_surface in phone/watch) attributes from
    the relay registry and does NOT consult active-surface.json (which stays the watch-only writer);
    a non-relay call (relay_surface None) falls back to the active-surface reader."""
    if relay_surface in _KNOWN_SURFACES:
        return {"device": relay_surface, "via": "relay"}
    return active_surface_fallback()


class RelayManager:
    """All live relay conversations. Sole owner of registry/buffer/surface/replay state."""

    def __init__(self, socket_factory=None, on_finalize=None, cap=3, idle_ttl_s=1800,
                 partials_enabled=None, partials_cadence_s=1.5, scribe_fn=None,
                 tombstone_ttl_s=600, connect_cooldown_s=2.0,
                 factories=None, vendor_fn=None, vendor_check=None,
                 hume_session_s=HUME_MAX_SESSION_S, usage=None,
                 idle_close=None, idle_close_s=None, vad_rms_floor=None,
                 soft_reopen_s=SOFT_REOPEN_S_DEFAULT,
                 speaking_enabled=None, speak_idle_ms=SPEAK_IDLE_MS,
                 refusal_ttl_s=600.0,
                 orphan_end=None, orphan_end_s=None, orphan_sweep_s=10.0,
                 agent_text_enabled=None):
        self.socket_factory = socket_factory or _default_socket_factory
        self.factories = dict(factories or {})
        self.factories.setdefault("hume", hume_socket_factory)
        # B1 hermeticity pin: a test-injected EL socket_factory with NO vendor plumbing must
        # never read the live vendor file (or touch the network) — pin it to elevenlabs so the
        # 25-test EL baseline stays hermetic even with state/voice-vendor.json={'vendor':'hume'}.
        _pinned = socket_factory is not None and factories is None
        self.vendor_fn = vendor_fn or ((lambda: "elevenlabs") if _pinned else voice_vendor.get_vendor)
        self.vendor_check = vendor_check or ((lambda v: "") if _pinned
                                             else voice_vendor.unavailable_reason)
        self.hume_session_s = hume_session_s
        self.usage = usage                 # Task-9 relay-integration wires add_seconds in end()
        self._conv_started = {}            # conversation_id -> monotonic start ts (usage, once)
        # idle-close (ARTURO_STREAM_IDLE_CLOSE, default OFF => byte-identical lifecycle)
        self.idle_close = idle_close_enabled() if idle_close is None else idle_close
        self.idle_close_s = (float(os.environ.get("ARTURO_IDLE_CLOSE_S", IDLE_CLOSE_S_DEFAULT))
                             if idle_close_s is None else idle_close_s)
        self.vad_rms_floor = (float(os.environ.get("ARTURO_VAD_RMS_FLOOR", VAD_RMS_FLOOR_DEFAULT))
                              if vad_rms_floor is None else vad_rms_floor)
        self.soft_reopen_s = soft_reopen_s
        # agent_speaking boundary (ARTURO_STREAM_SPEAKING, default OFF => byte-identical)
        self.speaking_enabled = (globals()["speaking_enabled"]()
                                 if speaking_enabled is None else speaking_enabled)
        self.speak_idle_ms = speak_idle_ms
        # Hume reply-text streaming (ARTURO_STREAM_AGENT_TEXT, default OFF => byte-identical)
        self.agent_text_enabled = (globals()["agent_text_enabled"]()
                                   if agent_text_enabled is None else agent_text_enabled)
        # Vendor credit/auth refusal cache (ios msg_bf132099): a live refusal (EL 3000 close,
        # Hume E0300 error event) blocks NEW conversations on that vendor VISIBLY at claim
        # (spec §6) until the TTL lapses or a healthy frame proves recovery.
        self.refusal_ttl_s = refusal_ttl_s
        self._vendor_refusals = {}     # vendor -> (message, ts)
        # Orphan reaper (ios msg_3107d68b: watch app died mid-call, socket+journal burned
        # until a manual /end). An orphan = NO uplink POST and NO events poll for
        # orphan_end_s -> full end(). Distinct from idle-close: a quiet-but-ALIVE client
        # keeps polling/POSTing silence and is never reaped.
        self.orphan_end = (os.environ.get("ARTURO_ORPHAN_END", "") == "1"
                           if orphan_end is None else orphan_end)
        self.orphan_end_s = (float(os.environ.get("ARTURO_ORPHAN_END_S", "60"))
                             if orphan_end_s is None else orphan_end_s)
        self._orphan_sweep_s = orphan_sweep_s
        self._last_contact = {}        # conversation_id -> last feed/poll ts
        self._shutdown = False
        # Daemon registry (gm msg_1f417cdd): /proc/<pid>/task/*/comm does NOT carry Python
        # thread names, so liveness gates need an in-process record + a startup log line.
        self.daemons = []
        if self.orphan_end:
            threading.Thread(target=self._orphan_sweeper, daemon=True,
                             name="relay-orphan-sweeper").start()
            self.daemons.append("orphan-sweeper")
            log.info(f"relay: orphan sweeper started ({self.orphan_end_s:.0f}s threshold)")
        self.on_finalize = on_finalize or (lambda cid: None)
        self.registry = ptt_stream.StreamRegistry(cap=cap, idle_ttl_s=idle_ttl_s)
        self.buffer = ptt_stream.EventBuffer()
        self.partials_enabled = (globals()["partials_enabled"]()
                                 if partials_enabled is None else partials_enabled)
        self.partials_cadence_s = partials_cadence_s
        self.scribe_fn = scribe_fn or _default_scribe
        self.tombstone_ttl_s = tombstone_ttl_s
        self.connect_cooldown_s = connect_cooldown_s
        self._holders = {}
        self._surface = {}          # conversation_id -> "watch" (relay knowledge beats inference)
        self._replay = {}           # conversation_id -> ReplayLog
        self._needs_replay = set()
        self._el_map = {}           # EL conversation id -> OUR conversation_id (criterion #5)
        self._tombstones = {}       # conversation_id -> end ts; TTL-bounded resurrection guard
        self._lock = threading.Lock()

    def map_el_id(self, el_id, conversation_id):
        with self._lock:
            self._el_map[el_id] = conversation_id

    def _orphan_sweeper(self):
        while not self._shutdown:
            time.sleep(min(self._orphan_sweep_s, max(self.orphan_end_s / 4, 0.05)))
            if self._shutdown:
                return
            now = time.time()
            with self._lock:
                orphans = [cid for cid in self._holders
                           if now - self._last_contact.get(cid, now) > self.orphan_end_s]
            for cid in orphans:
                log.warning(f"relay {cid}: ORPHANED (no uplink/poll for "
                            f"{self.orphan_end_s:.0f}s) — ending conversation")
                try:
                    self.end(cid)
                except Exception as e:
                    log.error(f"relay {cid}: orphan end failed: {e!r}")

    def record_vendor_refusal(self, vendor, message):
        with self._lock:
            self._vendor_refusals[vendor] = (message, time.time())

    def vendor_refusal(self, vendor):
        """The cached live refusal for a vendor, or None (TTL-pruned: self-healing)."""
        with self._lock:
            entry = self._vendor_refusals.get(vendor)
            if entry is None:
                return None
            message, ts = entry
            if time.time() - ts >= self.refusal_ttl_s:
                del self._vendor_refusals[vendor]
                return None
            return message

    def clear_vendor_refusal(self, vendor):
        with self._lock:
            self._vendor_refusals.pop(vendor, None)

    def prune_el_ids(self, conversation_id):
        """I3: drop a conversation's EL-id mappings (idle-close makes them historical; the next
        session's metadata re-maps). Bounds _el_map across repeated idle cycles."""
        with self._lock:
            for el_id in [k for k, v in self._el_map.items() if v == conversation_id]:
                del self._el_map[el_id]

    def resolve(self, callback_conv_id):
        """Resolve a custom-LLM callback's conversation id to OUR conversation_id: our own id
        passes through (live only); EL's id resolves via the metadata mapping; unknown -> None."""
        if not callback_conv_id:
            return None
        with self._lock:
            if callback_conv_id in self._surface:
                return callback_conv_id
            return self._el_map.get(callback_conv_id)

    def sole_live(self):
        """The single live relay conversation, or None when zero/ambiguous. The id-less-callback
        fallback (EL socket callbacks may carry NO conversation id at all — smoke-proven):
        single-user reality makes one-live unambiguous; >=2 refuses attribution."""
        with self._lock:
            return next(iter(self._surface)) if len(self._surface) == 1 else None

    def _tombstoned(self, conversation_id, now=None):
        """True if this id was ended within the TTL. Opportunistically prunes lapsed entries so
        the map cannot grow unbounded and so an id becomes reusable once its TTL passes."""
        now = time.time() if now is None else now
        with self._lock:
            for cid in [c for c, ts in self._tombstones.items() if now - ts >= self.tombstone_ttl_s]:
                del self._tombstones[cid]
            ts = self._tombstones.get(conversation_id)
            return ts is not None and now - ts < self.tombstone_ttl_s

    # -- api --
    def feed_audio(self, conversation_id, pcm, surface_device=None):
        # An ENDED conversation must never be resurrected by a late uplink callback (build-206
        # kept POSTing ~43s past End). Reject the tombstoned id outright -> route 410 (hard stop)
        # rather than re-claiming the slot and re-creating a holder that sole_live() then trips on.
        if os.environ.get(UPLINK_TAP_FLAG, "") == "1":
            _uplink_tap(conversation_id, pcm)     # before ANY gating: exactly what arrived
        if self._tombstoned(conversation_id):
            return {"ok": False, "error": "ended"}
        # Vendor read + refuse BEFORE claim/lock (spec §6 / grain-review S1): a NEW conversation
        # on an unavailable vendor is refused VISIBLY with the reason — no holder, no registry
        # claim, no socket. A LIVE holder keeps the vendor it started with (flips take effect
        # on the next conversation), so mid-call feeds are never refused by a preference flip.
        with self._lock:
            live = self._holders.get(conversation_id)
        if live is None:
            vendor = self.vendor_fn()
            reason = self.vendor_check(vendor)
            if reason:
                return {"ok": False, "error": "vendor_unavailable", "message": reason}
            refusal = self.vendor_refusal(vendor)
            if refusal:
                # live credit/auth refusal cached from the socket path: a flip to a
                # known-dead vendor is refused VISIBLY at claim, not greeting-then-silence
                return {"ok": False, "error": "vendor_unavailable", "message": refusal}
            if self.usage is not None and self.usage.over_cap(vendor):
                # spec §4.6: at 100% of the daily cap NEW conversations are refused visibly
                # (live calls run to completion; only fresh claims stop).
                return {"ok": False, "error": "vendor_unavailable",
                        "message": f"{vendor} daily voice cap reached"}
        else:
            vendor = live.vendor
        if not self.registry.claim(conversation_id):
            return {"ok": False, "error": "capacity"}    # REJECT-NEW; live never evicted
        with self._lock:
            h = self._holders.get(conversation_id)
            if h is None:
                h = self._holders[conversation_id] = _Holder(conversation_id, self, vendor)
                _dev = normalize_surface(surface_device)
                self._surface[conversation_id] = _dev      # stamped ONCE at creation (first chunk's X-Surface)
                log.info(f"surface: cid={conversation_id[:8]} device={_dev} x_surface={surface_device or '-'}")
                self._conv_started[conversation_id] = time.time()
            self._last_contact[conversation_id] = time.time()
        h.feed(pcm)
        return {"ok": True}

    def events(self, conversation_id, cursor=0):
        self.registry.touch(conversation_id)
        with self._lock:
            if conversation_id in self._holders:
                self._last_contact[conversation_id] = time.time()   # a polling client is ALIVE
        return self.buffer.since(conversation_id, cursor)

    def end(self, conversation_id):
        with self._lock:
            h = self._holders.pop(conversation_id, None)
            started = self._conv_started.pop(conversation_id, None)
            self._last_contact.pop(conversation_id, None)
            self._surface.pop(conversation_id, None)
            self._replay.pop(conversation_id, None)
            self._needs_replay.discard(conversation_id)
            for el_id in [k for k, v in self._el_map.items() if v == conversation_id]:
                del self._el_map[el_id]
            self._tombstones[conversation_id] = time.time()   # block late-callback resurrection
        self.buffer.drop(conversation_id)   # downlink hygiene: a reused cid at cursor=0 must
                                            # never replay this dead conversation's events
        self.registry.release(conversation_id)
        if h is None:
            return False
        if self.agent_text_enabled:
            # by-effect proof for the streamed reply-text contract (ios msg_3a5b4d4b): the
            # proxy access log carries no event bodies, so surface the counters at end.
            log.info(f"relay {conversation_id}: agent_response counters — "
                     f"partials={h.ar_partials} finals={h.ar_finals}")
        # Task 9: ONE usage record per conversation, keyed on _conv_started — resumes/reconnects
        # never split a call; add_seconds is fire-and-forget on alerts so this cannot block end().
        if self.usage is not None and started is not None:
            try:
                self.usage.add_seconds(h.vendor, time.time() - started)
            except Exception as e:
                log.error(f"relay usage record failed ({conversation_id}): {e}")
        h.close()
        try:
            self.on_finalize(conversation_id)            # server-driven finalize (no 900s wait)
        except Exception as e:
            log.error(f"relay finalize error ({conversation_id}): {e}")
        return True

    def is_ended(self, conversation_id):
        """Tombstoned-within-TTL check for the routes (the events route 410s an ended cid)."""
        return self._tombstoned(conversation_id)

    def surface(self, conversation_id):
        with self._lock:
            return self._surface.get(conversation_id)

    def replay_log(self, conversation_id):
        with self._lock:
            return self._replay.setdefault(conversation_id, ptt_stream.ReplayLog())

    def mark_needs_replay(self, conversation_id):
        with self._lock:
            self._needs_replay.add(conversation_id)

    def replay_block(self, conversation_id):
        """Consume-once reconnect context for the custom-LLM callback seam."""
        with self._lock:
            if conversation_id not in self._needs_replay:
                return ""
            self._needs_replay.discard(conversation_id)
            turns = self._replay.get(conversation_id)
        return ptt_stream.replay_context_block(turns.recent() if turns else [])

    def shutdown(self):
        self._shutdown = True
        with self._lock:
            holders = list(self._holders.values())
            self._holders.clear()
        for h in holders:
            h.close()
