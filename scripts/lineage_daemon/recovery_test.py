"""Tests — pre-retire context-assist (DEC-1787808620 A.3 rework)."""
from scripts.lineage_daemon.recovery import maybe_context_assist


def test_fires_when_struggling_and_predecessor_live():
    sent = []
    fired = maybe_context_assist("pred", "succ", struggling=True,
                                 predecessor_live_fn=lambda p: True,
                                 assist_fn=lambda p, s: sent.append((p, s)))
    assert fired is True and sent == [("pred", "succ")]


def test_no_assist_when_not_struggling():
    sent = []
    fired = maybe_context_assist("pred", "succ", struggling=False,
                                 predecessor_live_fn=lambda p: True,
                                 assist_fn=lambda p, s: sent.append((p, s)))
    assert fired is False and sent == []


def test_no_assist_when_predecessor_already_gone():
    sent = []
    fired = maybe_context_assist("pred", "succ", struggling=True,
                                 predecessor_live_fn=lambda p: False,
                                 assist_fn=lambda p, s: sent.append((p, s)))
    assert fired is False and sent == []


def test_assist_send_failure_is_swallowed():
    def boom(p, s):
        raise RuntimeError("inject failed")
    fired = maybe_context_assist("pred", "succ", struggling=True,
                                 predecessor_live_fn=lambda p: True, assist_fn=boom)
    assert fired is False       # never crashes the beat
