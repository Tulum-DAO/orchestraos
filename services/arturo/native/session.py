"""NativeSession: one native GPT-Live session driven by the server. Ported from the reviewed POC
(poc/gptlive_native/session.py); the boundary is now tools.ArturoTools (Arturo's real tools), not the mock fleet.

The session is a pure event processor: `on_server_frame(frame)` updates state and returns the client frames to send,
so offline tests drive it with synthetic frames and a replay drives it with a recorded trace. A Driver pumps a
transport (FakeTransport offline; the real socket only behind the BudgetGate) and does the sending and recording.

What it does with a frame, in documented terms (protocol.py cites each):
  session.started            -> audio may flow
  response.event             -> nested response.created: remember response_id for the delegation
                                nested response.output_item.done (function_call): collect the call (duplicates
                                counted, never executed twice)
                                nested terminal lifecycle with uncollected calls: run each through the mock
                                boundary, send one function_call_output per call, then ONE response.create (U2)
  session.*.appended         -> async delivery ack (accepted, never "heard")
  output audio / transcripts -> speech spans, first-audio latency, claims of completion (recorded, never trusted)
  error / info / unknown     -> recorded and counted
"""
import base64
import json
import logging
import re
import collections
import time
from dataclasses import dataclass, field

from . import native_config, protocol
from .cost import CostRecorder
from .tool_ledger import args_sha
from .tools import ArturoTools, Delivery, SessionBinding

log = logging.getLogger("arturo-native")
_DEFAULT = object()
# A boundary that runs tools off the audio loop returns this while a call is still running: the delegation stays held
# and is submitted on a later tick, when every one of its results is ready (a real tool can take up to 120 s).
RUNNING = {"ok": False, "error": "RUNNING", "detail": "this tool call is still running"}
# DOC live-delegation L187: "Send a response.item.create result for every pending function call"; the output is an
# application-defined string (L135 example '{"status":"confirmed",...}'). A superseded call gets this, never an effect.
SUPERSEDED_OUTPUT = {"status": "superseded", "note": "the caller kept talking; answered in the next request"}

SPAN_TAIL_S = 0.8            # same end-of-span rule as the relay (stream_relay OPENAI_SPAN_TAIL_S)
BARGE_S = 0.3                # same energy-barge rule as the relay (F5)
_CLAIM_RE = re.compile(r"\b(done|finished|completed|it'?s (all )?set|taken care of)\b", re.I)
# DROPPED-REQUEST (gm msg_bbfb14fb; field case arturo-latency-dev evidence-1126/dropped-request.md, run B1: "ask the GM
# ..." spoken while a read_screen_context delegation was out; GPT-Live never delegated it). From the frames we have,
# a request is SWALLOWED when: the caller began a NEW utterance (input transcript after a UTTERANCE_GAP_S pause) while
# a delegation was still out (created before that utterance began, its calls not all answered), and no delegation was
# created in the DROP_AFTER_S after the caller's last words. Logged once per utterance with the call id, the timings,
# the busy delegations and the lengths of what was heard / said (the words, redacted, go to the call report only);
# never acted on. Small talk with no delegation out is never one. It is a SIGNAL to investigate, not a verdict: an
# "ok, thanks" while a tool is held, a pause > UTTERANCE_GAP_S inside one request, or a request's own delegation
# created before its first transcript delta can each be reported; any new delegation clears the watch, even one that
# answers an earlier request.
UTTERANCE_GAP_S = 1.0
DROP_AFTER_S = 6.0          # normal: delegation 1.3-2.0 s after the caller stops (B1 l1a 1.3 s, C_def l1b 2.0 s)


class TransportDown(RuntimeError):
    pass


@dataclass
class Metrics:
    started_at: float = None
    first_audio_at: float = None
    user_end_at: float = None
    first_audio_after_user_end: list = field(default_factory=list)
    tool_requested_at: dict = field(default_factory=dict)       # call_id -> t
    tool_result_sent_at: dict = field(default_factory=dict)     # call_id -> t
    first_audio_after_result: dict = field(default_factory=dict)
    barges: list = field(default_factory=list)                  # (barge_t, last_speech_t or None)
    duplicate_call_events: int = 0
    hold_wait_s: dict = field(default_factory=dict)             # call_id -> seconds the settle/hold kept it
    dropped_requests: list = field(default_factory=list)        # DROPPED-REQUEST records (see DROP_AFTER_S)
    undocumented_types: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)


class NativeSession:
    def __init__(self, binding: SessionBinding, orchestra: ArturoTools, clock=time.monotonic, speech_fn=None,
                 settle=_DEFAULT, turn_speech_fn=None):
        if not isinstance(binding, SessionBinding):
            raise TypeError("NativeSession needs the server-made SessionBinding")
        self.binding = binding
        self.orchestra = orchestra
        self.delivery = Delivery(orchestra.ledger)
        self.clock = clock
        self._speech = speech_fn or _default_speech
        self._turn_speech = turn_speech_fn or _turn_speech
        self._turn_speech_last = None           # wall clock of the last uplink chunk
        self._uplink_audio_s = 0.0              # uplink audio seen so far, in audio seconds
        self._turn_win = collections.deque()    # (audio end s, speech s) over the last TURN_WINDOW_S of audio
        self.cost = CostRecorder()
        self.m = Metrics()
        self.started = False
        self.closed = None                      # close reason once session.closed arrives
        self.delegations = {}                   # delegation_id -> {"response_id", "calls": {call_id: (name, args)}}
        self.seen_calls = set()
        self.submitted_calls = set()
        self.tool_outputs = {}                  # call_id -> output dict (what the boundary returned)
        self.output_text = ""                   # assistant transcript, whole session
        self.turn_text = ""                     # assistant transcript since the last user speech
        self.turn_started_epoch = time.time()   # wall clock of the caller's latest turn (claim gate: "this turn")
        self.spoken_turn = ""                   # Arturo's words since the caller's latest turn (spawn read-back);
        #                                         never reset by a claim (review M5: _record_claim wiped turn_text)
        self._arturo_spoke = False              # Arturo spoke since the caller's latest turn began
        self._caller_spoke_after = False        # the caller's uplink carried speech after Arturo began this turn
        self.user_turn_text = ""                # the caller's words in the current turn (send_telegram's gate)
        self.claims = []                        # (text, task_id or None)
        self.acks = []                          # client_event_ids acknowledged by *.appended
        self._speaking = False
        self._quiet_s = 0.0
        self._last_speech_t = None
        self._user_speaking_since = None
        self._last_user_t = None
        self._barge_open = None
        # settle/hold (native_config.Settle; None = submit at once, the pre-hold behaviour)
        self.settle = native_config.SETTLE if settle is _DEFAULT else settle
        self._caller_last_t = None              # last caller activity: uplink speech or an input transcript delta
        self._spoke_after_caller = False        # GPT-Live started speaking >= quiet_s after the caller's last activity
        self._turn = 0                          # caller turns: +1 when the caller resumes after GPT-Live spoke
        self._order = []                        # delegation ids in creation order
        self._held = []                         # completed delegations whose calls are not submitted yet
        self._done = {}                         # (turn, name, args_sha) -> (call_id, output): L2 per-turn reuse
        self._utt_start = None                  # when the caller's current utterance began (transcript clock)
        self._drop_watch = None                 # an utterance spoken while a delegation was out (DROP_AFTER_S)
        self.delivery_kind = None               # () -> append kind for late results; None: the POC's rule
        self.deliveries_open = True             # False once the call ended: a late result goes to its thread instead

    # ---------------------------------------------------------------------------------------------------
    def on_server_frame(self, frame):
        now = self.clock()
        out = []
        kind, t = protocol.classify(frame)
        if kind == "undocumented":
            self.m.undocumented_types[t] = self.m.undocumented_types.get(t, 0) + 1
        self.cost.on_frame(frame)
        if t == "session.started":
            self.started, self.m.started_at = True, now
        elif t == "session.closed":
            self.closed = frame.get("reason") or "closed"
        elif t == "error":
            self.m.errors.append(frame.get("error") or {})
        elif t in ("session.instructions.appended", "session.thinking.appended", "session.commentary.appended"):
            cid = frame.get("client_event_id")
            if cid:
                self.acks.append(cid)
                self.delivery.on_ack(cid)
        elif t == "session.output_audio.delta":
            self._on_output_audio(frame, now)
        elif t == "session.output_transcript.delta":
            d = frame.get("delta") or ""
            self.output_text += d
            self.turn_text += d
            self.spoken_turn += d
            self._arturo_spoke = True
            if _CLAIM_RE.search(self.turn_text):
                self._record_claim()
        elif t == "session.input_transcript.delta":
            self._caller_activity(now)
            self._watch_request(now, frame.get("delta") or "")
            self._last_user_t = now
            self.m.user_end_at = now
            # A new caller turn (receipts, fence, read-back) needs the caller's own SPEECH after Arturo began, not
            # just a transcript delta: a late delta of the utterance that asked for a lookup would otherwise lift the
            # fence after the result came back (re-review N4).
            if self._arturo_spoke and self._caller_spoke_after:
                self._arturo_spoke = False
                self._caller_spoke_after = False
                self.turn_started_epoch = time.time()
                self.spoken_turn = ""
                self.user_turn_text = ""
                new_turn = getattr(self.orchestra, "new_turn", None)
                if new_turn:
                    new_turn(self.binding.conversation_id)
            self.user_turn_text += frame.get("delta") or ""
            self.turn_text = ""
        elif t == "session.delegation.created":
            d = frame.get("delegation") or {}
            self._delegation(d.get("id"), d.get("response_id"), d.get("target"))
        elif t == "response.event":
            out += self._on_response_event(frame, now)
        return [protocol.validate_client(f) for f in out]

    # ---- Responses delegation -------------------------------------------------------------------------
    def _on_response_event(self, frame, now):
        did, ev = protocol.nested(frame)
        deleg = self._delegation(did, None, "responses")
        et = ev.get("type")
        if et == "response.created":
            deleg["response_id"] = (ev.get("response") or {}).get("id")
            return []
        call = protocol.function_call_from(ev)
        if call:
            call_id, name, args = call
            if call_id in self.seen_calls:
                self.m.duplicate_call_events += 1          # U8: counted; the ledger also makes it a no-op
            else:
                self.seen_calls.add(call_id)
                self.m.tool_requested_at[call_id] = now
            deleg["calls"][call_id] = (name, args)
            return []
        if et in protocol.TERMINAL_NESTED:
            deleg["terminal"] = now
            if any(cid not in self.submitted_calls for cid in deleg["calls"]):
                deleg["ready"], deleg["turn"] = now, self._turn
                if did not in self._held:
                    self._held.append(did)
            return self._release(now)
        return []

    def _delegation(self, did, response_id, target):
        if did not in self.delegations:
            self.delegations[did] = {"response_id": response_id, "calls": {}, "target": target, "ready": None,
                                     "terminal": None, "created": self.clock()}
            self._order.append(did)
            self._drop_watch = None                  # the provider delegated: the caller's request was not lost
        elif response_id and not self.delegations[did]["response_id"]:
            self.delegations[did]["response_id"] = response_id
        return self.delegations[did]

    # ---- settle/hold (design msg_46477539, approved msg_54cc2391) -----------------------------------------
    def _caller_activity(self, now):
        if self._spoke_after_caller:
            self._turn += 1
            self._spoke_after_caller = False
        self._caller_last_t = now

    def _settled(self, now):
        """The caller has been quiet quiet_s AND (GPT-Live has started speaking since, OR quiet speak_or_s)."""
        if self.settle is None or self._caller_last_t is None:
            return True
        quiet = now - self._caller_last_t
        return quiet >= self.settle.quiet_s and (self._spoke_after_caller or quiet >= self.settle.speak_or_s)

    def tick(self, now=None):
        """Release held delegations whose time has come (the Driver calls this on every pump)."""
        now = self.clock() if now is None else now
        self._check_dropped(now)
        return [protocol.validate_client(f) for f in self._release(now)]

    # ---- DROPPED-REQUEST (see DROP_AFTER_S) ----------------------------------------------------------------
    def _busy(self, before):
        """Delegations created before `before` whose calls are not all answered (or that have not finished yet)."""
        return [did for did in self._order
                if self.delegations[did]["created"] < before
                and (self.delegations[did]["terminal"] is None
                     or any(c not in self.submitted_calls for c in self.delegations[did]["calls"]))]

    def _watch_request(self, now, delta):
        if self._last_user_t is None or now - self._last_user_t >= UTTERANCE_GAP_S:
            self._utt_start = now
            busy = self._busy(now)
            self._drop_watch = {"since": now, "busy": busy, "heard": "", "said": len(self.output_text)} if busy else None
        if self._drop_watch is not None:
            self._drop_watch["heard"] += delta

    def _check_dropped(self, now):
        w = self._drop_watch
        if w is None or self._last_user_t is None or now - self._last_user_t < DROP_AFTER_S:
            return
        self._drop_watch = None
        from services.arturo.thread_context import _redact
        heard, said = " ".join(w["heard"].split()), " ".join(self.output_text[w["said"]:].split())
        rec = {"at_s": round(w["since"] - (self.m.started_at or w["since"]), 2), "busy": w["busy"],
               "heard": _redact(heard)[:160], "said": _redact(said)[:160],
               "no_delegation_s": round(now - self._last_user_t, 2)}
        self.m.dropped_requests.append(rec)
        # the log carries no words (G6: a caller's words can carry amounts and merchants); the report has them, redacted
        log.warning(f"native {self.binding.conversation_id}: DROPPED-REQUEST the caller spoke {rec['at_s']} s into the "
                    f"call while delegation(s) {rec['busy']} were out; no delegation {rec['no_delegation_s']} s after "
                    f"their last words (heard {len(heard)} chars, Arturo said {len(said)} chars)")

    def _release(self, now):
        frames = []
        for did in list(self._held):
            d = self.delegations[did]
            expired = self.settle is not None and now - d["ready"] >= self.settle.hold_max_s
            newer = [] if self.settle is None else self._order[self._order.index(did) + 1:]
            if newer:
                # A newer delegation arrived while this one waited: the caller kept talking and GPT-Live re-asked.
                # Wait until every newer request has finished collecting its calls; a held call whose tool a newer
                # request also calls is SUPERSEDED (a result, never an effect); any other held call is a separate
                # request and runs.
                if not expired and not all(self.delegations[n]["terminal"] is not None for n in newer):
                    continue
                names = {name for n in newer for name, _ in self.delegations[n]["calls"].values()}
                sub = self._submit(did, now, superseded_names=names, by=newer[-1])
            elif self._settled(now) or expired:
                sub = self._submit(did, now)
            else:
                continue
            if sub is None:                                  # a tool is still running: stay held, retry next tick
                continue
            frames += sub
            self._held.remove(did)
        return frames

    def _submit(self, did, now, superseded_names=(), by=None):
        d = self.delegations[did]
        pending = [(cid, na) for cid, na in d["calls"].items() if cid not in self.submitted_calls]
        if not pending:
            return []
        # Every pending call's output first; if any tool is still RUNNING, submit nothing yet (None = stay held).
        outputs = []
        for call_id, (name, args) in pending:
            key = (d.get("turn", self._turn), name, args_sha(args))
            if name in superseded_names:
                outputs.append((call_id, name, args, key, "superseded", dict(SUPERSEDED_OUTPUT)))
            elif self.settle is not None and key in self._done and self._done[key][0] != call_id:
                outputs.append((call_id, name, args, key, "reused", self._done[key]))
            else:
                out = self.orchestra.execute(self.binding, call_id, name, args)
                outputs.append((call_id, name, args, key, "executed", out))
        if any(kind == "executed" and out is RUNNING for _, _, _, _, kind, out in outputs):
            return None
        frames = []
        for call_id, name, args, key, kind, out in outputs:
            if kind == "superseded":
                output = out
                self.orchestra.ledger.record_not_executed(call_id, self.binding.conversation_id, name, args,
                                                          "superseded", by, output)
            elif kind == "reused":
                prev_id, output = out
                self.orchestra.ledger.record_not_executed(call_id, self.binding.conversation_id, name, args,
                                                          "reused", prev_id, output)
            else:
                output = out
                self._done[key] = (call_id, output)
            self.tool_outputs[call_id] = output
            self.submitted_calls.add(call_id)
            self.m.tool_result_sent_at[call_id] = now
            if d.get("ready") is not None:
                self.m.hold_wait_s[call_id] = round(now - d["ready"], 3)
            frames.append(protocol.function_call_output(call_id, output))
        frames.append(protocol.response_create())          # ONE continue after ALL results (DOC delegation)
        return frames

    # ---- speech / barge-in -------------------------------------------------------------------------------
    def _on_output_audio(self, frame, now):
        try:
            pcm = base64.b64decode(frame.get("delta") or "")
        except Exception:  # noqa: BLE001
            pcm = b""
        speech = self._speech(pcm)
        if speech:
            self._quiet_s = 0.0
            self._last_speech_t = now
            if (self._caller_last_t is not None and self.settle is not None
                    and now - self._caller_last_t >= self.settle.quiet_s):
                self._spoke_after_caller = True
            if not self._speaking:
                self._speaking = True
                if self.m.first_audio_at is None:
                    self.m.first_audio_at = now
                if self.m.user_end_at is not None:
                    self.m.first_audio_after_user_end.append(round(now - self.m.user_end_at, 3))
                    self.m.user_end_at = None
                for cid, t_sent in self.m.tool_result_sent_at.items():
                    self.m.first_audio_after_result.setdefault(cid, round(now - t_sent, 3))
        else:
            self._quiet_s += len(pcm) / 32000.0
            if self._speaking and self._quiet_s >= SPAN_TAIL_S:
                self._speaking = False
                if self._barge_open is not None:
                    self.m.barges.append((self._barge_open, self._last_speech_t))
                    self._barge_open = None

    def on_uplink(self, pcm, now=None):
        """The harness feeds the user's audio here too, for the energy barge-in (no provider event exists, U4)."""
        now = self.clock() if now is None else now
        # A new caller turn needs real speech at the barge floor (re-review N4b: the 60 RMS floor let noise or echo
        # open a turn, and a late delta then lifted the fence): TURN_SPEECH_S of it within the last TURN_WINDOW_S of
        # uplink AUDIO (round 4 N4c: wall-clock timing failed a short "yes", a dip inside a word and big client frames).
        if self._turn_speech_last is not None and now - self._turn_speech_last > TURN_STREAM_GAP_S:
            self._turn_win.clear()                             # the stream paused: older audio is another moment
        self._turn_speech_last = now
        dur = len(pcm or b"") / 32000.0                        # 16 kHz s16 mono
        self._uplink_audio_s += dur
        v = self._turn_speech(pcm)              # speech seconds in this chunk (a detector may answer a bool)
        speech = dur if v is True else float(v or 0.0)
        self._turn_win.append((self._uplink_audio_s, min(speech, dur)))
        while self._turn_win and self._uplink_audio_s - self._turn_win[0][0] >= TURN_WINDOW_S:
            self._turn_win.popleft()
        if sum(d for _, d in self._turn_win) >= TURN_SPEECH_S - 1e-9:
            self.caller_spoke()
        if self._speech(pcm):
            self._caller_activity(now)
            self._user_speaking_since = self._user_speaking_since or now
            if self._speaking and self._barge_open is None and now - self._user_speaking_since >= BARGE_S:
                self._barge_open = now
        else:
            self._user_speaking_since = None

    def caller_spoke(self):
        """The caller is speaking (uplink energy; offline: a typed 'say'). Speech after Arturo began opens the next
        caller turn when its transcript arrives."""
        if self._arturo_spoke:
            self._caller_spoke_after = True

    @property
    def assistant_speaking(self):
        return self._speaking

    @property
    def user_speaking(self):
        return self._user_speaking_since is not None

    # ---- claims + async delivery ---------------------------------------------------------------------
    def _record_claim(self):
        task_ids = [o.get("task_id") for o in self.tool_outputs.values() if o.get("task_id")]
        tid = task_ids[-1] if task_ids else None
        self.claims.append((self.turn_text[-160:], tid))
        if tid:
            self.orchestra.ledger.note_model_claim(tid, "completed", self.turn_text[-160:])
        self.turn_text = ""

    def delivery_frames(self):
        """(task_id, frame) pairs to send now; call delivery_sent() for each one the transport accepted."""
        if not self.deliveries_open:
            return []
        return [(tid, protocol.validate_client(f)) for tid, f in self.delivery.frames_for(
            self.binding.conversation_id, self.user_speaking, self.assistant_speaking,
            kind=self.delivery_kind() if self.delivery_kind else None)]

    def delivery_sent(self, task_id, frame):
        self.delivery.mark_sent(task_id, frame)
        fence = getattr(self.orchestra, "fence", None)     # the note's text cannot steer an action (I5): fenced from
        if fence is not None:                               # the moment it is in the call, whatever turn this is
            fence(self.binding.conversation_id)

    def report(self):
        return {
            "conversation_id": self.binding.conversation_id, "principal": self.binding.principal,
            "started": self.started, "closed": self.closed,
            "tool_calls": sorted(self.seen_calls), "submitted": sorted(self.submitted_calls),
            "duplicate_call_events": self.m.duplicate_call_events,
            "ledger_duplicate_hits": self.orchestra.ledger.duplicate_hits(),
            "tasks": len(self.orchestra.ledger.tasks()),
            "first_audio_after_user_end_s": self.m.first_audio_after_user_end,
            "first_audio_after_result_s": self.m.first_audio_after_result,
            "barge_to_last_speech_s": [round(s - b, 3) if s else None for b, s in self.m.barges],
            "claims": self.claims, "acks": self.acks, "errors": self.m.errors,
            "undocumented_types": self.m.undocumented_types, "cost": self.cost.summary(),
            "deliveries": self.delivery.states(self.binding.conversation_id),
            "hold_wait_s": self.m.hold_wait_s,
            "dropped_requests": self.m.dropped_requests,
            "not_executed": [{k: r[k] for k in ("tool_call_id", "name", "status", "ref")}
                             for r in self.orchestra.ledger.not_executed()],
            "settle": None if self.settle is None else self.settle.__dict__,
        }


TURN_SPEECH_FLOOR = 500     # the server's own speech floor for spend activity and the barge (server.py)
TURN_SPEECH_S = 0.2         # speech (audio seconds) that opens a caller turn ...
TURN_WINDOW_S = 0.6         # ... within this much uplink audio
TURN_STREAM_GAP_S = 2.0     # a pause in the uplink stream this long starts a fresh window


def _turn_speech(pcm):
    """Seconds of this chunk spent in 20 ms windows above the barge floor (round 5 N14: crediting the whole chunk let
    two clicks open a turn)."""
    import numpy as np
    n = len(pcm or b"") // 2
    if n == 0:
        return 0.0
    x = np.frombuffer(pcm[: n * 2], "<i2").astype(np.float32)
    k = n // 320
    if k == 0:
        return n / 16000.0 if float(np.sqrt((x * x).mean())) > TURN_SPEECH_FLOOR else 0.0
    w = x[: k * 320].reshape(k, 320)
    return float((np.sqrt((w * w).mean(axis=1)) > TURN_SPEECH_FLOOR).sum()) * 0.02


def _default_speech(pcm):
    from services.arturo import openai_live
    return bool(pcm) and openai_live.is_speech(pcm)


class FakeTransport:
    """Offline transport: the test or a replay pushes server frames; sent frames are kept. drop() injects a
    disconnect: every later send/recv raises TransportDown."""

    def __init__(self):
        self.sent = []
        self.inbox = []
        self.down = False

    def push(self, frame):
        self.inbox.append(json.dumps(frame))

    def send(self, payload):
        if self.down:
            raise TransportDown("dropped")
        self.sent.append(json.loads(payload))

    def recv(self):
        if self.down:
            raise TransportDown("dropped")
        if not self.inbox:
            return None
        return self.inbox.pop(0)

    def drop(self):
        self.down = True

    def close(self):
        self.down = True


class Driver:
    """Pumps a transport into a NativeSession; records every frame both ways."""

    def __init__(self, session: NativeSession, transport, recorder=None):
        self.s, self.t, self.rec = session, transport, recorder
        self.disconnected = False

    def send(self, frame):
        protocol.validate_client(frame)
        if self.rec:
            self.rec.record("out", frame)
        try:
            self.t.send(json.dumps(frame))
        except TransportDown:
            self.disconnected = True
            raise

    def pump(self, max_frames=10_000):
        """Process every frame available now. Returns the number processed; stops quietly on a disconnect."""
        n = 0
        while n < max_frames:
            try:
                raw = self.t.recv()
            except TransportDown:
                self.disconnected = True
                return n
            if raw is None:
                break
            frame = json.loads(raw)
            if self.rec:
                self.rec.record("in", frame)
            for f in self.s.on_server_frame(frame):
                try:
                    self.send(f)
                except TransportDown:
                    return n + 1
            n += 1
        for f in self.s.tick():                            # held delegations whose settle time has come
            try:
                self.send(f)
            except TransportDown:
                return n
        for task_id, f in self.s.delivery_frames():
            try:
                self.send(f)
            except TransportDown:
                break                                   # the row stays 'pending': the next session re-sends it
            self.s.delivery_sent(task_id, f)
        return n
