"""Tests for the self-trigger rotation seam (gm commission msg_93fd71f5).

The PRIMARY rotation trigger is self-accomplished: a live PostToolUse hook reads the
agent's OWN ctx% and, at the rotation threshold, injects a one-time self-rotate
instruction + writes a MARK. The safety-net beat (ob's lane) reads that mark to tell
'self-triggered (skip)' vs 'crossed-threshold-but-silent = wedged (nudge)'. This module
is the SHARED CONTRACT: the JS hook writes marks in this shape; ob's beat calls classify.
Pure/no-IO — same discipline as the S3 observed-dict + event_schema.
"""
from scripts.focus_registry.rotation_signal import (
    rotation_level,
    should_fire,
    build_mark,
    classify,
    ROTATION_REMAINING,
    SELF_TRIGGERED, WEDGED, OK,
)


# --- rotation_level: remaining% (lower = more used) -> escalating level ---

def test_no_level_above_threshold():
    assert rotation_level(35) is None
    assert rotation_level(12.1) is None


def test_author_level_just_below_12():
    assert rotation_level(12) == "author"
    assert rotation_level(11) == "author"


def test_rotate_level_at_10():
    assert rotation_level(10) == "rotate"
    assert rotation_level(9) == "rotate"


def test_immediate_level_at_8():
    assert rotation_level(8) == "immediate"
    assert rotation_level(2) == "immediate"


def test_rotation_remaining_default_is_10():
    assert ROTATION_REMAINING == 10


def test_default_mark_max_age_is_co_signed_2700():
    # ob co-sign: 2700s (45min) > a full author->spawn->orient->cite-back->cooldown
    # ->two-sample rotation envelope, so a slow-but-alive self-rotation isn't
    # reclassified wedged mid-flight; < 3600 so a truly wedged agent is caught <~45min.
    from scripts.focus_registry.rotation_signal import DEFAULT_MARK_MAX_AGE_S
    assert DEFAULT_MARK_MAX_AGE_S == 2700


def test_classify_uses_2700_default_boundary():
    m = build_mark("sid", "%3", "/x", 9.0, "rotate", ts=1000.0)
    # 44 min old with the co-signed default -> still self_triggered (alive, rotating).
    assert classify(True, m, now=1000.0 + 2640) == SELF_TRIGGERED
    # 46 min old -> wedged (past the 2700 backstop).
    assert classify(True, m, now=1000.0 + 2760) == WEDGED


# --- should_fire: once per LEVEL, escalation re-fires at a higher severity ---

def test_fires_on_first_time_at_a_level():
    assert should_fire("author", last_fired=None) is True


def test_does_not_refire_same_level():
    assert should_fire("author", last_fired="author") is False


def test_escalation_refires_at_higher_severity():
    assert should_fire("rotate", last_fired="author") is True
    assert should_fire("immediate", last_fired="rotate") is True


def test_does_not_fire_when_no_level():
    assert should_fire(None, last_fired=None) is False
    assert should_fire(None, last_fired="author") is False


def test_never_regresses_to_lower_severity():
    # if we already fired 'immediate', a later 'author' reading does not re-fire.
    assert should_fire("author", last_fired="immediate") is False


# --- build_mark: the durable seam signal the hook writes ---

def test_build_mark_carries_source_identity_and_level():
    m = build_mark(session_id="sid-1", pane="%3", cwd="/x",
                   remaining=9.0, level="rotate", ts=1000.0)
    assert m["kind"] == "rotation_self_trigger"
    assert m["session_id"] == "sid-1"
    assert m["pane"] == "%3"
    assert m["cwd"] == "/x"
    assert m["level"] == "rotate"
    assert m["remaining"] == 9.0
    assert m["triggered_at"] == 1000.0


# --- classify: the handoff-of-responsibility predicate (ob's beat calls this) ---

def test_not_crossed_threshold_is_ok():
    assert classify(crossed_threshold=False, mark=None, now=1000.0) == OK


def test_crossed_with_fresh_mark_is_self_triggered():
    m = build_mark("sid", "%3", "/x", 9.0, "rotate", ts=1000.0)
    assert classify(crossed_threshold=True, mark=m, now=1000.0 + 30) == SELF_TRIGGERED


def test_crossed_with_no_mark_is_wedged():
    # crossed the rotation threshold but never self-triggered -> the beat must nudge.
    assert classify(crossed_threshold=True, mark=None, now=1000.0) == WEDGED


def test_crossed_with_stale_mark_is_wedged():
    # a mark far in the past means it self-triggered earlier but is NOW silent past
    # threshold again (didn't complete the rotation) -> wedged, nudge.
    m = build_mark("sid", "%3", "/x", 9.0, "rotate", ts=1000.0)
    assert classify(crossed_threshold=True, mark=m, now=1000.0 + 100000,
                    max_age_s=3600) == WEDGED


def test_classify_fail_open_on_malformed_mark():
    # a malformed mark (no triggered_at) is treated as absent -> wedged when crossed
    # (fail toward the beat nudging; never silently skip a possibly-wedged agent).
    assert classify(crossed_threshold=True, mark={"kind": "x"}, now=1000.0) == WEDGED
