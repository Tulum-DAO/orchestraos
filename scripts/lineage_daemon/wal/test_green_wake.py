"""GOAL-FINDINGS #3 (confirm-started) + #4 (wake-green-to-ingest) primitives, v2.1 addendum
DEC-1789425184694395. Provider-agnostic — all provider effects are injected fakes."""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import green_wake as gw  # noqa: E402


# ---- #3 confirm-started (Ink submit-race, bounded Enter-resend) ----

def test_started_immediately_no_reenter():
    reenters = []
    ok = gw.confirm_turn_started(lambda: True, lambda: reenters.append(1))
    assert ok is True and reenters == []          # already started -> zero resends


def test_started_after_two_reenters():
    state = {"n": 0}
    reenters = []

    def probe():
        # not started until the 2nd re-enter has been sent (Ink swallowed the first Enter)
        return len(reenters) >= 2

    def reenter():
        reenters.append(1)

    ok = gw.confirm_turn_started(probe, reenter, max_retries=3)
    assert ok is True and len(reenters) == 2       # recovered by the 2nd resend


def test_never_starts_returns_false_after_bounded_retries():
    reenters = []
    ok = gw.confirm_turn_started(lambda: False, lambda: reenters.append(1), max_retries=3)
    assert ok is False                              # caller raises GreenDidNotStart / alarms
    assert len(reenters) == 3                       # bounded — exactly max_retries resends


# ---- #4 wake-green-to-ingest ----

def test_wake_injects_then_confirms_started():
    injected = []
    ok = gw.wake_green_to_ingest(
        inject_fn=lambda t: injected.append(t),
        probe_started_fn=lambda: True,
        reenter_fn=lambda: None)
    assert ok is True
    assert injected and "ingest" in injected[0].lower()   # the wake actually injected


def test_wake_fails_loud_when_green_never_starts():
    ok = gw.wake_green_to_ingest(
        inject_fn=lambda t: None,
        probe_started_fn=lambda: False,
        reenter_fn=lambda: None,
        max_retries=2)
    assert ok is False           # never started -> caller must alarm LOUD, keep NOT-READY


# ---- (c) idempotent wake: send once, resend only on green-IDLE, never queue duplicates ----

def test_wake_idempotent_busy_green_delivers_exactly_one_prompt():
    """(c): the live re-fire queued 6 duplicate 'ingest…' prompts because _wake_and_verify
    is called every PREWARMING beat and re-injected each time. Across 6 beats with a BUSY
    green (never idle, turn not yet started), the ingest text must be delivered EXACTLY ONCE
    — subsequent beats must NOT queue a duplicate behind the boot turn."""
    injected = []
    sent = {"v": False}

    for _ in range(6):   # 6 PREWARMING beats
        gw.wake_green_to_ingest(
            inject_fn=lambda t: injected.append(t),
            probe_started_fn=lambda: False,        # turn hasn't started yet (still booting)
            reenter_fn=lambda: None,
            probe_idle_fn=lambda: False,           # green BUSY every beat (boot turn running)
            already_sent_fn=lambda: sent["v"],
            mark_sent_fn=lambda: sent.__setitem__("v", True),
            max_retries=1, sleep_fn=lambda: None)

    assert injected.count("ingest your hydrate digest, then hold") == 1, \
        f"expected exactly 1 prompt across 6 busy beats, got {len(injected)}"


def test_wake_resends_only_when_idle_after_first_send():
    """After the one send, a resend is allowed ONLY once the green is IDLE (its boot turn
    ended) — so the nudge lands on an empty composer, never queued behind a running turn."""
    injected = []
    sent = {"v": False}
    reenters = []

    def call(idle, started):
        return gw.wake_green_to_ingest(
            inject_fn=lambda t: injected.append(t),
            probe_started_fn=lambda: started,
            reenter_fn=lambda: reenters.append(1),
            probe_idle_fn=lambda: idle,
            already_sent_fn=lambda: sent["v"],
            mark_sent_fn=lambda: sent.__setitem__("v", True),
            max_retries=1, sleep_fn=lambda: None)

    call(idle=True, started=True)     # beat 1: first send, turn starts cleanly (no nudge)
    assert injected.count("ingest your hydrate digest, then hold") == 1 and reenters == []
    call(idle=False, started=False)   # beat 2: busy -> NO resend (Enter), NO new text
    assert reenters == [] and injected.count("ingest your hydrate digest, then hold") == 1
    call(idle=True, started=False)    # beat 3: idle -> a bounded Enter nudge is allowed
    assert reenters == [1] and injected.count("ingest your hydrate digest, then hold") == 1


def test_wake_already_started_short_circuits_no_reinject():
    """If a prior beat's ingest turn already STARTED, the waker returns True without
    re-injecting (idempotent) — never a duplicate prompt on a green that's already ingesting."""
    injected = []
    ok = gw.wake_green_to_ingest(
        inject_fn=lambda t: injected.append(t),
        probe_started_fn=lambda: True,
        reenter_fn=lambda: None,
        probe_idle_fn=lambda: True,
        already_sent_fn=lambda: True,        # sent on a prior beat
        mark_sent_fn=lambda: None)
    assert ok is True and injected == []     # already started -> no re-inject


# ---- #4 ingestion-verified (READY gate) ----

def test_ready_only_when_ingested_through_h():
    assert gw.ingestion_verified(lambda g: 10, "g", 8) is True     # 10 >= 8
    assert gw.ingestion_verified(lambda g: 5, "g", 8) is False     # 5 < 8, not ready
    assert gw.ingestion_verified(lambda g: None, "g", 8) is False  # unknown -> fail-closed
    assert gw.ingestion_verified(lambda g: 10, "g", None) is False # nothing delivered yet


def test_ready_fail_closed_when_probe_raises():
    def boom(g):
        raise RuntimeError("probe down")
    assert gw.ingestion_verified(boom, "g", 5) is False
