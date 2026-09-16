"""Tests — A.3 rework: completion-mode promote (defer-to-progressing-watch + item#4
auto-resume) and the POST-retire effect-watch (DEC-1787808620 / re-congruence
DEC-1787817982). NO rollback (the operator rejected rollback-to-predecessor as a RAM hog):
the predecessor is already retired promptly, so effect-present just RESOLVES and a
timeout ESCALATES LOUDLY while the successor keeps running.
"""
from scripts.lineage_daemon import complete as C

L_SID = "sess-successor-L"
PRED_SID = "sess-predecessor"
HOLD = {"canary": "pm-x", "successor": "pm-x-g2", "status": "hold:successor-unconfirmed",
        "reason": "same-beat", "created_at": 1000, "resolved": False}
_EFFECT = {"kind": "commit", "target": "abc123def"}


def _seams(**over):
    d = dict(
        now=5000,
        live_sid_fn=lambda a: L_SID,
        l_start_time_fn=lambda sid: 1500.0,
        readback_mtime_fn=lambda a: 2000.0,
        comprehension_mtime_fn=lambda a: 2100.0,
        provenance_sid_fn=lambda a: None,
        canonical_store_sids_fn=lambda c: (PRED_SID, PRED_SID, PRED_SID),
        confirm_fn=lambda c, s: {"outcome": "confirmed"},
        grade_fn=lambda c, s, sid: {"disposition": "PASS", "strict": True,
                                    "artifact_written": True},
        safety_fn=lambda c: ("SUPERSEDED_SAFE", "ok"),
        graduation_fn=lambda alias: True,
        promote_fn=lambda c, alias: {"ok": True},
        retire_fn=lambda c: {"retired": True},
        armed=True,
        budget={"per_beat_slot": True, "hourly_remaining": 4, "cooldown_clear": True},
    )
    d.update(over)
    return d


# ---- completion-mode promote: defer retire to the PROGRESSING watch + auto-resume ----

def test_completion_mode_promotes_defers_retire_to_progressing_watch():
    retires, resumes = [], []
    tr = C.complete_held_rotation(
        "pm-x", "pm-x-g2", HOLD, completion_mode=True, first_effect=_EFFECT,
        auto_resume_fn=lambda c, a, eff: resumes.append((c, a, eff)),
        **_seams(retire_fn=lambda c: retires.append(1) or {"retired": True}))
    assert tr["status"] == C.AWAITING_PROGRESS       # predecessor ALIVE for the progressing-watch
    assert tr["promoted"] is True and tr["retired"] is False
    assert retires == []                             # NOT retired at the promote beat
    assert resumes == [("pm-x", "pm-x-g2", _EFFECT)]  # item#4 auto-resume fired
    assert "auto_resume_inject" in tr["steps"]


def test_completion_mode_still_runs_all_gates_before_promote():
    tr = C.complete_held_rotation(
        "pm-x", "pm-x-g2", HOLD, completion_mode=True, first_effect=_EFFECT,
        auto_resume_fn=lambda c, a, eff: None,
        **_seams(graduation_fn=lambda a: False))
    assert tr["status"] == C.HOLD_NOT_GRADUATED
    assert tr["promoted"] is False


# ---- POST-retire effect-watch: present->COMPLETED / timeout->ESCALATE / no rollback ----

def _promoted_hold(**over):
    h = {**HOLD, "promoted_at": 5000, "status": C.RETIRED_AWAITING_EFFECT}
    h.update(over)
    return h


def test_effect_present_resolves_completed_no_retire():
    """Predecessor already retired promptly -> effect-present just RESOLVES. No retire,
    no escalate."""
    retires, escalated = [], []
    tr = C.complete_effect_verify(
        "pm-x", "pm-x-g2", _promoted_hold(), now=5000 + 900,
        effect_check_fn=lambda: True,
        escalate_fn=lambda c, s, ctx: escalated.append(s),
        retire_fn=lambda c: retires.append(c))
    assert tr["status"] == C.COMPLETED
    assert retires == [] and escalated == []


def test_effect_absent_within_window_keeps_waiting():
    tr = C.complete_effect_verify(
        "pm-x", "pm-x-g2", _promoted_hold(), now=5000 + 900,   # 1 beat (< 2)
        effect_check_fn=lambda: False,
        escalate_fn=lambda c, s, ctx: None, retire_fn=lambda c: None)
    assert tr["status"] == C.AWAITING_EFFECT


def test_effect_absent_past_window_escalates_loud_successor_keeps_running():
    escalated = []
    tr = C.complete_effect_verify(
        "pm-x", "pm-x-g2", _promoted_hold(), now=5000 + 3 * 900,  # past window
        effect_check_fn=lambda: False,
        escalate_fn=lambda c, s, ctx: escalated.append((s, ctx["reason"])),
        retire_fn=lambda c: None)
    assert tr["status"] == C.ESCALATED and tr["escalated"] is True
    assert escalated and escalated[0][0] == "pm-x-g2"
    assert tr.get("retired") is not True             # successor NOT killed
