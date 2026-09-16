"""Tests for the canary scoping guard (canary.py).

should_act is the single airtight gate: nothing is ever actionable
unless a canary is set AND the agent_id matches it exactly.
"""

from scripts.lineage_daemon.canary import should_act


def test_match_is_actionable():
    assert should_act("x", "x") is True


def test_mismatch_not_actionable():
    assert should_act("x", "y") is False


def test_no_canary_not_actionable():
    assert should_act("x", None) is False


def test_none_agent_not_actionable():
    assert should_act(None, "x") is False
