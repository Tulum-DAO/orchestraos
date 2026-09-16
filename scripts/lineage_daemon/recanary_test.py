"""Pytest wrapper for the content-bearing re-canary (WS3 v2 §4.2).

Keeps the end-to-end six-step acceptance in the CI suite: the walkthrough's main()
returns 0 iff every §4.2 invariant holds by effect (NEG-1 HELD, POS confirmed on
the planted decision, NEG-2 not churned, two-sample/A-with-guard/lock/ledger/reap).
Also asserts the two load-bearing negatives directly at the beat level so a
regression names itself.
"""
from scripts.lineage_daemon import recanary
from scripts.lineage_daemon import execute as ex


def test_recanary_walkthrough_all_invariants_hold():
    recanary.FAILS.clear()
    rc = recanary.main()
    assert rc == 0, f"re-canary invariants broken: {recanary.FAILS}"
    assert recanary.FAILS == []


def test_neg1_shallow_gen7_is_held_at_beat_level():
    trace, fake = recanary._hard_beat(
        recanary._confirm_over(recanary.shallow_evidence()))
    assert trace["status"] == ex.HOLD_UNCONFIRMED
    assert "retire" not in fake.calls
    assert len(trace["ledger"]["holds"]) == 1


def test_pos_deep_cites_planted_decision_confirms_and_retires():
    trace, fake = recanary._hard_beat(
        recanary._confirm_over(recanary.deep_evidence()))
    assert trace["status"] == ex.DONE
    assert "retire" in fake.calls and "repin_canonical" in fake.calls


def test_neg2_healthy_lagging_not_churned():
    trace, fake = recanary._hard_beat(
        recanary._confirm_over(recanary.deep_evidence(), lagging=True))
    assert trace["status"] == ex.DONE


def test_redacted_handoff_holds_no_canary_answers():
    redacted = recanary.planted_handoff().to_successor_init()
    assert all("answer" not in q for q in redacted["canary_questions"])
