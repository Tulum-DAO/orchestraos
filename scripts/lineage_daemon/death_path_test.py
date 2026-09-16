"""H3 death-path verifier assignment (WS3 v2, DEC-1786724046)."""

from scripts.lineage_daemon.death_path import (
    is_death_triggered, verification_plan,
    VERIFY_LIVE_PREDECESSOR, VERIFY_HOLD_FOR_REVIEW,
)


def test_is_death_triggered_by_death_field():
    assert is_death_triggered({"death": "oom", "reason": "death:oom"}) is True


def test_is_death_triggered_by_reason_prefix():
    assert is_death_triggered({"death": None, "reason": "death:court"}) is True


def test_not_death_triggered_ctx():
    assert is_death_triggered({"death": None, "reason": "ctx:HARD"}) is False
    assert is_death_triggered({}) is False


def test_ctx_path_uses_live_predecessor_and_may_auto_retire():
    plan = verification_plan({"death": None, "reason": "ctx:HARD"})
    assert plan["mode"] == VERIFY_LIVE_PREDECESSOR
    assert plan["auto_retire"] is True
    assert plan["verifier"] == "live-predecessor"
    assert plan["notify"] is None


def test_death_path_holds_for_review_no_auto_retire():
    plan = verification_plan({"death": "crash", "reason": "death:crash"})
    assert plan["mode"] == VERIFY_HOLD_FOR_REVIEW
    assert plan["auto_retire"] is False          # nothing to retire; pred is dead
    assert plan["mark"] == "unconfirmed"
    assert plan["notify"] == "gm"
    assert plan["verifier"] == "fresh-context"   # NEVER the dead predecessor
