"""Tests — complete_progressing_watch (A.3 rework PRE-retire phase, DEC-1787808620 /
re-congruence DEC-1787817982). RAM-safe: the predecessor retires within a BOUNDED
window (on-progress OR at the bound) and can never linger.
"""
from scripts.lineage_daemon import complete as C

BUDGET_UNUSED = None


def _watch(**over):
    calls = {"retire": [], "assist": [], "escalate": []}
    args = dict(
        now=1000 + 900,
        progressing_fn=lambda s: True,
        context_assist_fn=lambda c, s: calls["assist"].append(s),
        retire_fn=lambda c: calls["retire"].append(c),
        escalate_fn=lambda c, s, ctx: calls["escalate"].append(s),
        pre_retire_bound_beats=2,
    )
    args.update(over)
    hold = args.pop("hold", {"promoted_at": 1000, "status": C.AWAITING_PROGRESS})
    tr = C.complete_progressing_watch("pm-x", "pm-x-g2", hold, **args)
    return tr, calls


def test_progressing_confirmed_prompt_retires_and_enters_effect_watch():
    tr, calls = _watch(progressing_fn=lambda s: True)
    assert tr["status"] == C.RETIRED_AWAITING_EFFECT
    assert tr["retired"] is True
    assert calls["retire"] == ["pm-x"] and calls["escalate"] == [] and calls["assist"] == []
    assert tr["steps"].index("progressing_watch") < tr["steps"].index("retire")


def test_struggling_within_bound_fires_context_assist_and_waits():
    tr, calls = _watch(progressing_fn=lambda s: False, now=1000 + 900)  # 1 beat (< bound 2)
    assert tr["status"] == C.AWAITING_PROGRESS
    assert tr["retired"] is False
    assert calls["assist"] == ["pm-x-g2"]           # enrichment to the live predecessor
    assert calls["retire"] == [] and calls["escalate"] == []


def test_struggling_assist_only_when_predecessor_live():
    tr, calls = _watch(progressing_fn=lambda s: False, now=1000 + 900,
                       predecessor_live_fn=lambda c: False)
    assert tr["status"] == C.AWAITING_PROGRESS
    assert calls["assist"] == []                     # predecessor gone -> no assist, no crash


def test_never_progressing_past_bound_retires_anyway_and_escalates_loud():
    tr, calls = _watch(progressing_fn=lambda s: False, now=1000 + 3 * 900)  # past bound
    assert tr["status"] == C.RETIRED_ESCALATED
    assert tr["retired"] is True and tr["escalated"] is True
    assert calls["retire"] == ["pm-x"]              # RAM-first: predecessor gone, never lingers
    assert calls["escalate"] == ["pm-x-g2"]         # LOUD human surface
    assert tr["steps"].index("retire") < tr["steps"].index("escalate")


def test_no_rollback_vocabulary_remains():
    assert not hasattr(C, "ROLLED_BACK")
    assert not hasattr(C, "HOLD_ROLLBACK_FAILED")
    assert not hasattr(C, "PROMOTED_AWAITING_EFFECT")
