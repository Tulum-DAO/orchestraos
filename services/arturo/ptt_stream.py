"""v2/(b) stream-relay pure machinery (ARCHITECTURE-B.md @ sha256 85c97fdee64a, frozen —
gm msg_ad043c03 RED-first authorization; DEC-1788843712854271 CONSENSUS_REACHED).

I/O-free/thread-safe building blocks for the server-held EL Conversational-AI relay:
  StreamRegistry — per-conversation socket-holder slots: hard cap with REJECT-NEW semantics
    (a live conversation is NEVER evicted for a new one; a reconnect of an existing
    conversation_id is not 'new'), LRU-touch + idle-TTL eviction (a pocketed watch stops
    burning EL minutes).
  EventBuffer  — per-conversation cursor-resumable bounded event queue with TTL; backs BOTH
    downlink transports (SSE resume after a dropped stream, and long-poll since-cursor).
  UplinkGate   — drop-oldest ~2s audio buffer for the EL-socket-down window; signals
    'reconnecting' ONCE per outage so the client can surface state (congruence reviewer
    item #2: never a silent mid-utterance drop); the POST path never blocks.
  TurnPcmBuffer— per-turn growing PCM buffer (tail-capped): the single source both for
    our-side replay assembly and the option-2 partials fork.
  ReplayLog    — last-N-turns transcript for our-side context replay across an EL reconnect
    (fresh socket = fresh EL session; continuity is OURS — probe-corroborated).
  PartialsEngine — the operator-directed option-2 live user partials (gm msg_9d3f33f6 RED criteria):
    decimated cadence (~1.5-2s of NEW audio), single-flight, latest-wins, monotonic revision
    counter, EL-final supersedes/closes, scribe failure silently skipped (best-effort garnish
    that can NEVER touch the relay path).
"""
import json
import threading
import time


class StreamRegistry:
    def __init__(self, cap=3, idle_ttl_s=1800):
        self.cap = cap
        self.idle_ttl_s = idle_ttl_s
        self._lock = threading.Lock()
        self._live = {}          # conversation_id -> last_touch ts

    def _evict_idle(self, now):
        for cid, ts in list(self._live.items()):
            if now - ts >= self.idle_ttl_s:
                del self._live[cid]

    def claim(self, conversation_id, now=None):
        """True if this conversation may hold a socket. Existing id always passes (reconnect);
        a NEW id is REJECTED at cap — never evicts a live conversation."""
        now = now or time.time()
        with self._lock:
            self._evict_idle(now)
            if conversation_id in self._live:
                self._live[conversation_id] = now
                return True
            if len(self._live) >= self.cap:
                return False
            self._live[conversation_id] = now
            return True

    def has(self, conversation_id):
        with self._lock:
            return conversation_id in self._live

    def touch(self, conversation_id, now=None):
        with self._lock:
            if conversation_id in self._live:
                self._live[conversation_id] = now or time.time()

    def release(self, conversation_id):
        with self._lock:
            self._live.pop(conversation_id, None)

    def live_ids(self):
        with self._lock:
            return list(self._live)


class EventBuffer:
    def __init__(self, cap=500, ttl_s=300):
        self.cap = cap
        self.ttl_s = ttl_s
        self._lock = threading.Lock()
        self._q = {}             # conversation_id -> {"events": [(cursor, event)], "next": int, "ts": float}

    def put(self, conversation_id, event):
        with self._lock:
            s = self._q.setdefault(conversation_id, {"events": [], "next": 1, "ts": time.time()})
            s["events"].append((s["next"], event))
            s["next"] += 1
            s["ts"] = time.time()
            if len(s["events"]) > self.cap:
                s["events"] = s["events"][-self.cap:]

    def since(self, conversation_id, cursor=0):
        """(events_after_cursor, new_cursor)."""
        with self._lock:
            s = self._q.get(conversation_id)
            if not s:
                return [], cursor
            out = [(c, e) for c, e in s["events"] if c > cursor]
            new_cursor = out[-1][0] if out else cursor
            return [e for _, e in out], new_cursor

    def since_page(self, conversation_id, cursor=0, max_bytes=131072, max_audio=8):
        """Capped page of since(): (events, new_cursor, more) — downlink-amplification fix
        (ios msg_2f3e8d3c, vc_2b106dfe: same-cursor long-polls re-transferred a 1.1MB audio
        backlog over a degraded watch link, starving the uplink). Cursor-ordered slice that
        stops BEFORE the event that would exceed max_bytes of serialized payload or
        max_audio audio events, whichever first; events are never split; new_cursor is the
        LAST INCLUDED event's own cursor so the client resumes exactly there; more=True when
        truncated. Progress guarantee: the first event is always included even if alone it
        exceeds max_bytes — a single oversized event must never wedge the stream."""
        with self._lock:
            s = self._q.get(conversation_id)
            if not s:
                return [], cursor, False
            pending = [(c, e) for c, e in s["events"] if c > cursor]
        if not pending:
            return [], cursor, False
        out, used_bytes, used_audio = [], 0, 0
        for c, e in pending:
            size = len(json.dumps(e))
            is_audio = e.get("type") == "audio"
            if out and (used_bytes + size > max_bytes
                        or (is_audio and used_audio + 1 > max_audio)):
                return [e2 for _, e2 in out], out[-1][0], True
            out.append((c, e))
            used_bytes += size
            used_audio += 1 if is_audio else 0
        return [e2 for _, e2 in out], out[-1][0], False

    def drop(self, conversation_id):
        """Forget a conversation's events NOW (end-of-call hygiene: a reused client cid at
        cursor=0 must never replay a dead conversation — ios msg_84512683)."""
        with self._lock:
            self._q.pop(conversation_id, None)

    def sweep(self, now=None):
        now = now or time.time()
        with self._lock:
            for cid in [c for c, s in self._q.items() if now - s["ts"] >= self.ttl_s]:
                del self._q[cid]


class UplinkGate:
    """Buffers uplink audio while the EL socket is down. Drop-OLDEST beyond ~max_buffer_s;
    buffer() returns True exactly once per outage (the 'signal reconnecting' edge)."""

    def __init__(self, max_buffer_s=2.0, chunk_s=0.25):
        self.max_chunks = max(1, int(max_buffer_s / chunk_s))
        self._lock = threading.Lock()
        self._chunks = []
        self._outage_signaled = False

    def buffer(self, chunk):
        with self._lock:
            signal = not self._outage_signaled
            self._outage_signaled = True
            self._chunks.append(chunk)
            if len(self._chunks) > self.max_chunks:
                self._chunks = self._chunks[-self.max_chunks:]
            return signal

    def drain(self):
        with self._lock:
            out, self._chunks = self._chunks, []
            self._outage_signaled = False
            return out


class TurnPcmBuffer:
    """Per-turn growing PCM buffer, tail-capped (most recent audio wins)."""

    def __init__(self, max_bytes=1_000_000):
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._buf = b""

    def append(self, pcm):
        with self._lock:
            self._buf += pcm
            if len(self._buf) > self.max_bytes:
                self._buf = self._buf[-self.max_bytes:]

    def pcm(self):
        with self._lock:
            return self._buf

    def tail(self, n_bytes):
        """Last n_bytes of the turn (whole buffer if shorter) — the O(T) partials window."""
        with self._lock:
            return self._buf[-n_bytes:] if 0 < n_bytes < len(self._buf) else self._buf

    def end_turn(self):
        with self._lock:
            self._buf = b""


class ReplayLog:
    """Last-N-turns transcript for our-side replay at the callback seam after an EL reconnect."""

    def __init__(self, max_turns=6):
        self.max_turns = max_turns
        self._lock = threading.Lock()
        self._turns = []         # (role, text) — a 'turn' here = one utterance either side

    def add(self, role, text):
        with self._lock:
            self._turns.append((role, text))
            # cap counts PAIRS conservatively: keep max_turns*2 utterances
            if len(self._turns) > self.max_turns * 2:
                self._turns = self._turns[-self.max_turns * 2:]

    def recent(self):
        with self._lock:
            return list(self._turns)

    def clear(self):
        with self._lock:
            self._turns = []


def replay_context_block(turns):
    """Context block re-seeding the brain after an EL reconnect (fresh EL session has no
    history — continuity is ours, keyed on OUR conversation_id)."""
    if not turns:
        return ""
    lines = ["=== RECONNECT CONTEXT (this live call continued across a connection drop — "
             "the turns below already happened; continue naturally, do not re-greet) ==="]
    for role, text in turns:
        lines.append(f"{'the operator' if role == 'user' else 'You'}: {text}")
    return "\n".join(lines)


class PartialsEngine:
    """Option-2 live user partials (the operator-directed, gm-ruled RED criteria).

    feed(pcm) accumulates the turn's audio; every `cadence_s` of NEW audio one scribe pass
    runs over the LAST `window_s` seconds of the buffer (the O(T) fix, DEC-1788855344537432:
    re-STTing the whole growing buffer was O(T^2) and burned 21.5k credits in ~10 min) in a
    worker thread (single-flight — a slow pass never queues a second). A MANDATORY
    per-conversation Scribe-seconds budget (`budget_s`) makes runaway impossible even if the
    window logic has a bug: the check runs BEFORE the single-flight semaphore is acquired
    (a blocked pass must never leak it) and the counter survives turn boundaries — it resets
    only with a new conversation (new _Holder => new engine). emit_fn receives
    {"type": "user_partial", "text", "revision"} with a monotonic revision counter (client
    renders replace-not-append). turn_final() closes the sequence (EL's final supersedes);
    turn_start() re-opens for the next turn. Scribe failures are skipped silently — partials
    are best-effort garnish and NEVER block or touch the relay path.
    """

    def __init__(self, scribe_fn, emit_fn, cadence_s=1.5, bytes_per_s=32000, max_bytes=1_000_000,
                 window_s=12.0, budget_s=300.0):
        self.scribe_fn = scribe_fn
        self.emit_fn = emit_fn
        self.cadence_bytes = int(cadence_s * bytes_per_s)
        self.bytes_per_s = bytes_per_s
        self.window_bytes = int(window_s * bytes_per_s)
        self.budget_s = budget_s
        self._lock = threading.Lock()
        self._buf = TurnPcmBuffer(max_bytes=max_bytes)
        self._since_last = 0
        self._revision = 0
        self._closed = False
        self._turn = 1            # stamped on every user_partial (client keys (turn, revision))
        self._inflight = threading.BoundedSemaphore(1)
        self._active = 0          # worker threads alive (for wait_idle in tests)
        self._scribe_seconds_used = 0.0   # per-CONVERSATION; never reset at turn boundaries
        self._budget_alerted = False

    def current_turn(self):
        with self._lock:
            return self._turn

    def turn_start(self):
        with self._lock:
            self._closed = False
            self._since_last = 0
            self._turn += 1       # a worker snapshot from the PREVIOUS turn can never match now
        self._buf.end_turn()

    def turn_final(self):
        with self._lock:
            self._closed = True
            self._since_last = 0
        self._buf.end_turn()

    def feed(self, pcm):
        with self._lock:
            if self._closed:
                return
            self._since_last += len(pcm)
            fire = self._since_last >= self.cadence_bytes
        self._buf.append(pcm)
        if not fire:
            return
        # O(T) window: the pass sees only the trailing window, never the whole buffer.
        snapshot = self._buf.tail(self.window_bytes)
        window_seconds = len(snapshot) / self.bytes_per_s
        # Budget gate BEFORE the semaphore (congruence must-fix #2): a budget-blocked pass
        # must never acquire — leaking BoundedSemaphore(1) would wedge partials for good.
        with self._lock:
            if self._scribe_seconds_used + window_seconds > self.budget_s:
                alert = not self._budget_alerted
                self._budget_alerted = True
            else:
                alert = None
        if alert is not None:
            if alert:
                try:
                    self.emit_fn({"type": "partials_budget_exhausted"})
                except Exception:
                    pass
            return
        if not self._inflight.acquire(blocking=False):
            return                # single-flight: worker busy; the next cadence crossing retries
        with self._lock:
            self._since_last = 0
            self._active += 1
            snap_turn = self._turn
            self._scribe_seconds_used += window_seconds   # billed at dispatch, capped by the gate

        def _work():
            try:
                text = self.scribe_fn(snapshot)
                with self._lock:
                    # turn-boundary safety (contract add msg_4bfda30d, the 179 phantom-row
                    # class): NEVER emit after this turn's final, and NEVER emit a stale
                    # worker's text into a LATER turn — the snapshot's turn must still be
                    # the current, open turn.
                    if self._closed or self._turn != snap_turn:
                        return
                    self._revision += 1
                    rev = self._revision
                if text:
                    self.emit_fn({"type": "user_partial", "text": text,
                                  "revision": rev, "turn": snap_turn})
            except Exception:
                pass              # best-effort: failed partial is silently skipped
            finally:
                with self._lock:
                    self._active -= 1
                self._inflight.release()

        threading.Thread(target=_work, daemon=True, name="ptt-stream-partial").start()

    def wait_idle(self, timeout_s=2.0):
        """Test helper: wait for in-flight partial workers to drain."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            with self._lock:
                if self._active == 0:
                    return True
            time.sleep(0.02)
        return False
