"""RED-first — live-partials O(T) cost fix (arturo-partials-otn-fix-spec.md,
DEC-1788855344537432 CONSENSUS_REACHED honest @bdf987eb). Sliding-window re-STT
(snapshot = buffer tail, never the whole growing buffer) + MANDATORY per-conversation
Scribe-seconds budget cap. Kept out of test_stream_relay.py (25 EL baseline untouched)."""
import threading
import time

from services.arturo import ptt_stream
from services.arturo import stream_relay as sr


class SpyScribe:
    def __init__(self):
        self.snapshots = []
        self._lock = threading.Lock()

    def __call__(self, pcm):
        with self._lock:
            self.snapshots.append(len(pcm))
        return "partial text"

    @property
    def lengths(self):
        with self._lock:
            return list(self.snapshots)


def _engine(spy, emitted, **kw):
    kw.setdefault("cadence_s", 1.0)
    kw.setdefault("bytes_per_s", 1000)      # 1000 bytes == 1 "second" for easy math
    kw.setdefault("window_s", 2.0)          # WINDOW_BYTES = 2000
    kw.setdefault("budget_s", 1000.0)
    return ptt_stream.PartialsEngine(scribe_fn=spy, emit_fn=emitted.append, **kw)


def _feed_and_drain(e, chunk, n):
    for _ in range(n):
        e.feed(chunk)
        e.wait_idle()


def test_partials_restt_uses_tail_window_not_whole_buffer():
    spy, emitted = SpyScribe(), []
    e = _engine(spy, emitted)
    _feed_and_drain(e, b"\x00" * 1000, 6)          # buffer grows to 6000 bytes
    assert spy.lengths, "no scribe pass fired"
    assert max(spy.lengths) <= 2000, f"snapshot exceeded WINDOW_BYTES: {spy.lengths}"


def test_partials_cost_is_linear_not_quadratic():
    spy, emitted = SpyScribe(), []
    e = _engine(spy, emitted)
    _feed_and_drain(e, b"\x00" * 1000, 20)
    passes = len(spy.lengths)
    assert sum(spy.lengths) <= passes * 2000       # linear: passes x constant window
    # the quadratic shape would have summed ~ triangular growth toward 20k-byte snapshots
    assert max(spy.lengths) <= 2000


def test_partials_budget_cap_stops_runaway():
    spy, emitted = SpyScribe(), []
    e = _engine(spy, emitted, budget_s=6.0)        # 3 full 2s windows
    _feed_and_drain(e, b"\x00" * 1000, 40)         # unbounded single turn, no turn_final
    assert spy.lengths, "no pass ever fired"
    assert max(spy.lengths) <= 2000                # window holds even under inflation
    assert e._scribe_seconds_used <= 6.0 + 1e-9    # billed seconds never exceed the cap
    n_after_cap = len(spy.lengths)
    _feed_and_drain(e, b"\x00" * 1000, 10)
    assert len(spy.lengths) == n_after_cap, "passes kept firing past the budget"
    assert emitted.count({"type": "partials_budget_exhausted"}) == 1   # one-shot beat


def test_partials_budget_is_per_conversation():
    spy, emitted = SpyScribe(), []
    e = _engine(spy, emitted, budget_s=4.0)
    _feed_and_drain(e, b"\x00" * 1000, 3)
    e.turn_final()
    e.turn_start()
    _feed_and_drain(e, b"\x00" * 1000, 30)
    assert e._scribe_seconds_used <= 4.0 + 1e-9    # accumulates ACROSS turns, capped overall


def test_budget_survives_turn_boundary():
    spy, emitted = SpyScribe(), []
    e = _engine(spy, emitted)
    e._scribe_seconds_used = 1.23
    e.turn_final()                                  # the stream_relay.py user-final sequence
    e.turn_start()
    assert e._scribe_seconds_used == 1.23


def test_budget_blocked_pass_does_not_leak_semaphore():
    spy, emitted = SpyScribe(), []
    e = _engine(spy, emitted, budget_s=2.0)         # one 2s window exhausts it
    _feed_and_drain(e, b"\x00" * 1000, 4)           # exhaust
    _feed_and_drain(e, b"\x00" * 1000, 4)           # blocked passes
    assert e._inflight.acquire(blocking=False), "semaphore leaked by a budget-blocked pass"
    e._inflight.release()


def test_partial_text_still_emitted_within_window():
    spy, emitted = SpyScribe(), []
    e = _engine(spy, emitted)
    _feed_and_drain(e, b"\x00" * 1000, 2)
    partials = [x for x in emitted if x.get("type") == "user_partial"]
    assert partials and partials[0]["text"] == "partial text"
    assert partials[0]["turn"] == 1 and partials[0]["revision"] == 1


def test_partials_flag_off_byte_identical():
    spy = SpyScribe()
    m = sr.RelayManager(socket_factory=lambda cid: (_ for _ in ()).throw(RuntimeError("no")),
                        partials_enabled=False, scribe_fn=spy)
    try:
        m.feed_audio("c1", b"\x00" * 4000)
        assert m._holders["c1"].partials is None    # engine never constructed
        time.sleep(0.1)
        assert spy.lengths == []
    finally:
        m.shutdown()


def test_turn_pcm_buffer_tail():
    b = ptt_stream.TurnPcmBuffer()
    b.append(b"abcdef")
    assert b.tail(4) == b"cdef"
    assert b.tail(10) == b"abcdef"                  # whole buffer when shorter
