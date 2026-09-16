"""RED-first tests — v2/(b) pure stream-relay machinery (services/arturo/ptt_stream.py).

Build target frozen: ARCHITECTURE-B.md @ sha256 85c97fdee64a (gm msg_ad043c03).
Covers the pure halves of gm's locked criteria: REJECT-NEW registry, cursor-resumable event
buffer, drop-oldest uplink backpressure + reconnecting surfacing, per-turn PCM buffer (replay
+ partials fork), and the option-2 partials engine RED list (decimated single-flight,
latest-wins, monotonic revisions, final-supersedes, scribe-fail never blocks).
"""
import threading
import time

import pytest

from services.arturo import ptt_stream as ps


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    # defense against cross-file env bleed (gm gate must-fix msg_26a41e81): these pure tests
    # must never see the stream flags another test file exported.
    monkeypatch.delenv("ARTURO_STREAM_RELAY", raising=False)
    monkeypatch.delenv("ARTURO_STREAM_PARTIALS", raising=False)


# ---------- StreamRegistry: cap=REJECT-NEW, live never evicted, reconnect=existing ----------

def test_registry_cap_rejects_new_never_evicts_live():
    reg = ps.StreamRegistry(cap=3, idle_ttl_s=600)
    assert reg.claim("c1") and reg.claim("c2") and reg.claim("c3")
    assert not reg.claim("c4"), "cap hit -> REJECT-NEW"
    assert reg.has("c1") and reg.has("c2") and reg.has("c3"), "live conversations never evicted"
    assert reg.claim("c2"), "reconnect of an EXISTING conversation_id is not 'new' — must pass at cap"


def test_registry_idle_ttl_evicts_and_reopens_capacity():
    reg = ps.StreamRegistry(cap=2, idle_ttl_s=0.2)
    reg.claim("c1"); reg.claim("c2")
    assert not reg.claim("c3")
    time.sleep(0.3)
    reg.touch("c2")                      # c2 stays fresh
    assert reg.claim("c3"), "idle-TTL eviction of c1 must reopen capacity"
    assert not reg.has("c1") and reg.has("c2")


def test_registry_release_frees_slot():
    reg = ps.StreamRegistry(cap=1, idle_ttl_s=600)
    assert reg.claim("c1")
    reg.release("c1")
    assert reg.claim("c2")


# ---------- EventBuffer: cursor-resumable, bounded, TTL ----------

def test_event_buffer_cursor_resume():
    eb = ps.EventBuffer(cap=100, ttl_s=60)
    eb.put("c1", {"type": "user_transcript", "text": "hello"})
    eb.put("c1", {"type": "agent_response", "text": "hi"})
    events, cur = eb.since("c1", cursor=0)
    assert [e["type"] for e in events] == ["user_transcript", "agent_response"]
    eb.put("c1", {"type": "audio"})
    events2, cur2 = eb.since("c1", cursor=cur)
    assert [e["type"] for e in events2] == ["audio"], "cursor must resume exactly after the last read"
    assert cur2 > cur


def test_event_buffer_bounded_and_ttl():
    eb = ps.EventBuffer(cap=5, ttl_s=0.2)
    for i in range(10):
        eb.put("c1", {"type": "audio", "i": i})
    events, _ = eb.since("c1", cursor=0)
    assert len(events) == 5 and events[0]["i"] == 5, "bounded: oldest dropped"
    time.sleep(0.3)
    eb.sweep()
    events, _ = eb.since("c1", cursor=0)
    assert events == [], "TTL sweep clears stale conversations"


# ---------- UplinkGate: drop-oldest ~2s + reconnecting surfaced, POST never blocks ----------

def test_uplink_gate_drop_oldest_bounded():
    gate = ps.UplinkGate(max_buffer_s=2.0, chunk_s=0.25)   # 8 chunks max
    for i in range(12):
        gate.buffer(bytes([i]) * 100)
    chunks = gate.drain()
    assert len(chunks) == 8, "bounded to ~2s"
    assert chunks[0][0] == 4, "drop-OLDEST: first 4 dropped"


def test_uplink_gate_signals_reconnecting_once_per_outage():
    gate = ps.UplinkGate(max_buffer_s=2.0, chunk_s=0.25)
    assert gate.buffer(b"x") is True          # first buffered chunk of an outage -> signal
    assert gate.buffer(b"y") is False         # not re-signaled every chunk
    gate.drain()                              # reconnected
    assert gate.buffer(b"z") is True, "a NEW outage signals again"


# ---------- TurnPcmBuffer: replay + partials fork source ----------

def test_turn_pcm_buffer_grows_and_resets():
    tb = ps.TurnPcmBuffer(max_bytes=1000)
    tb.append(b"\x01" * 300); tb.append(b"\x02" * 300)
    assert len(tb.pcm()) == 600
    tb.end_turn()
    assert tb.pcm() == b"", "buffer resets per turn"


def test_turn_pcm_buffer_capped():
    tb = ps.TurnPcmBuffer(max_bytes=500)
    tb.append(b"\x01" * 400); tb.append(b"\x02" * 400)
    assert len(tb.pcm()) <= 500, "bounded (keeps the TAIL — most recent audio wins)"
    assert tb.pcm()[-1] == 2


# ---------- ReplayLog: our-side context replay across EL reconnect ----------

def test_replay_log_keeps_last_n_turns():
    rl = ps.ReplayLog(max_turns=3)
    for i in range(5):
        rl.add("user", f"u{i}"); rl.add("agent", f"a{i}")
    turns = rl.recent()
    assert len(turns) == 3 * 2
    assert turns[0] == ("user", "u2") and turns[-1] == ("agent", "a4")
    block = ps.replay_context_block(turns)
    assert "u4" in block and "a4" in block and "RECONNECT" in block.upper()


# ---------- PartialsEngine (option-2 RED list, gm msg_9d3f33f6) ----------

class SlowScribe:
    def __init__(self, delay=0.0, fail=False):
        self.calls = []
        self.delay = delay
        self.fail = fail
    def __call__(self, pcm):
        self.calls.append(len(pcm))
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("scribe down")
        return f"partial-{len(self.calls)}"


def test_partials_decimated_cadence():
    """Re-STT fires only per ~1.5s of NEW audio, not per chunk."""
    scribe = SlowScribe()
    emitted = []
    pe = ps.PartialsEngine(scribe_fn=scribe, emit_fn=lambda e: emitted.append(e),
                           cadence_s=1.5, bytes_per_s=32000)
    for _ in range(6):                       # 6 x 0.25s = 1.5s of audio
        pe.feed(b"\x00" * 8000)
    pe.wait_idle(2)
    assert len(scribe.calls) == 1, "exactly one scribe pass per cadence window"
    for _ in range(5):                       # +1.25s -> below next cadence threshold
        pe.feed(b"\x00" * 8000)
    pe.wait_idle(2)
    assert len(scribe.calls) == 1
    pe.feed(b"\x00" * 8000)                  # crosses 3.0s total
    pe.wait_idle(2)
    assert len(scribe.calls) == 2


def test_partials_single_flight_and_latest_wins():
    """A slow scribe call never queues a second; a newer window supersedes (latest-wins)."""
    scribe = SlowScribe(delay=0.4)
    emitted = []
    pe = ps.PartialsEngine(scribe_fn=scribe, emit_fn=lambda e: emitted.append(e),
                           cadence_s=0.25, bytes_per_s=32000)
    for _ in range(12):                      # keep crossing cadence while scribe is slow
        pe.feed(b"\x00" * 8000)
        time.sleep(0.05)
    pe.wait_idle(3)
    assert len(scribe.calls) <= 3, f"single-flight must prevent queue pileup (got {len(scribe.calls)})"
    revs = [e["revision"] for e in emitted]
    assert revs == sorted(revs) and len(set(revs)) == len(revs), "monotonic revision counter"


def test_partials_final_supersedes_and_closes():
    scribe = SlowScribe()
    emitted = []
    pe = ps.PartialsEngine(scribe_fn=scribe, emit_fn=lambda e: emitted.append(e),
                           cadence_s=0.1, bytes_per_s=32000)
    pe.feed(b"\x00" * 8000)
    pe.wait_idle(2)
    n_before = len(scribe.calls)
    pe.turn_final()                          # EL final arrived
    pe.feed(b"\x00" * 8000)                  # stale audio after final must not re-fire
    pe.wait_idle(1)
    assert len(scribe.calls) == n_before, "final closes the partial sequence for the turn"
    pe.turn_start()
    pe.feed(b"\x00" * 8000)
    pe.wait_idle(2)
    assert len(scribe.calls) == n_before + 1, "next turn re-opens cleanly"


def test_partials_scribe_failure_never_raises():
    scribe = SlowScribe(fail=True)
    emitted = []
    pe = ps.PartialsEngine(scribe_fn=scribe, emit_fn=lambda e: emitted.append(e),
                           cadence_s=0.1, bytes_per_s=32000)
    pe.feed(b"\x00" * 8000)                  # must not raise
    pe.wait_idle(2)
    assert emitted == [], "failed partials are silently skipped (best-effort garnish)"


def test_partials_events_are_real_replace_contract():
    scribe = SlowScribe()
    emitted = []
    pe = ps.PartialsEngine(scribe_fn=scribe, emit_fn=lambda e: emitted.append(e),
                           cadence_s=0.1, bytes_per_s=32000)
    pe.feed(b"\x00" * 8000)
    pe.wait_idle(2)
    assert emitted and emitted[0]["type"] == "user_partial"
    assert emitted[0]["text"] == "partial-1" and emitted[0]["revision"] == 1


# ---------- option-2 contract add (ios-watch-dev msg_4bfda30d): turn-id + post-final safety ----------
# HERMETIC discipline (gm gate must-fix msg_26a41e81): these two tests are condition-driven
# with generous ceilings and explicit sync points — NO fixed-sleep races, NO reliance on the
# box being idle, NO shared/module state (each builds its own engine). Green under any
# ordering and any load.

def _wait_for(pred, timeout_s=10.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_partials_stamped_with_turn_id():
    scribe = SlowScribe()
    emitted = []
    pe = ps.PartialsEngine(scribe_fn=scribe, emit_fn=lambda e: emitted.append(e),
                           cadence_s=0.1, bytes_per_s=32000)
    pe.feed(b"\x00" * 8000)
    assert _wait_for(lambda: any(e.get("turn") == 1 for e in emitted)), \
        f"turn-1 partial expected, emitted={emitted}"
    pe.turn_final()
    pe.turn_start()
    pe.feed(b"\x00" * 8000)
    assert _wait_for(lambda: any(e.get("turn") == 2 for e in emitted)), \
        f"turn id must increment per turn, emitted={emitted}"


def test_stale_worker_never_emits_into_next_turn():
    """A scribe pass in flight when the final lands must NOT emit after turn_start — stale
    text stamped into the NEXT turn is the 179 phantom-row class."""
    release = threading.Event()
    started = threading.Event()
    emitted = []
    def slow_scribe(pcm):
        started.set()                # explicit sync: the worker is definitively IN FLIGHT
        release.wait(10)
        return "stale text from turn 1"
    pe = ps.PartialsEngine(scribe_fn=slow_scribe, emit_fn=lambda e: emitted.append(e),
                           cadence_s=0.1, bytes_per_s=32000)
    pe.feed(b"\x00" * 8000)          # worker launches, blocks in slow_scribe
    assert started.wait(10), "worker must be in flight before the final lands"
    pe.turn_final()                  # EL final arrives while worker in flight
    pe.turn_start()                  # next turn opens
    release.set()                    # stale worker completes NOW
    assert pe.wait_idle(10), "worker must drain"
    assert emitted == [], "post-final/cross-turn partial suppressed server-side"
