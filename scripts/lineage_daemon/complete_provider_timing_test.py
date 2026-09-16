"""RED tests — timing instrumentation wired into build_completion_provider (spec §B).

Opt-in via `timing_sink`: when supplied, the provider wraps its completion seams with
the rotation_timing decorator and emits section rows. It is PURE-ADDITIVE — the beat's
returned status/promoted/retired are IDENTICAL to an un-instrumented run.
"""
from scripts.lineage_daemon import complete as C
from scripts.lineage_daemon import rotation_timing as RT

L_SID = "sess-successor-L"
PRED_SID = "sess-predecessor"
_EFFECT = {"kind": "commit", "target": "abc123def"}
_BUDGET = {"per_beat_slot": True, "hourly_remaining": 4, "cooldown_clear": True}


def _seams(rows=None, **over):
    calls = {"promote": [], "retire": [], "resume": []}
    d = dict(
        live_sid_fn=lambda a: L_SID,
        l_start_time_fn=lambda sid: 1500.0,
        readback_mtime_fn=lambda a: 2000.0,
        comprehension_mtime_fn=lambda a: 2100.0,
        provenance_sid_fn=lambda a: None,
        canonical_store_sids_fn=lambda c: (PRED_SID, PRED_SID, PRED_SID),
        confirm_fn=lambda c, s: {"outcome": "confirmed"},
        grade_fn=lambda c, s, sid: {"disposition": "PASS"},
        safety_fn=lambda c: ("SUPERSEDED_SAFE", "ok"),
        graduation_fn=lambda alias: True,
        promote_fn=lambda c, alias: calls["promote"].append((c, alias)) or {"ok": True},
        retire_fn=lambda c: calls["retire"].append(c) or {"retired": True},
        completion_mode=True,
        first_effect_fn=lambda c: _EFFECT,
        auto_resume_fn=lambda c, a, eff: calls["resume"].append(1),
        progressing_fn=lambda s: True,
        context_assist_fn=lambda c, s: None,
        escalate_fn=lambda c, s, ctx: None,
        predecessor_live_fn=lambda c: True,
    )
    if rows is not None:
        d["timing_sink"] = rows.append
    d.update(over)
    return d, calls


def _hold(**over):
    h = {"canary": "pm-x", "successor": "pm-x-g2",
         "status": "hold:successor-unconfirmed", "created_at": 0, "resolved": False}
    h.update(over)
    return h


def test_instrumented_promote_beat_is_behaviorally_identical():
    rows = []
    seams, calls = _seams(rows=rows)
    prov = C.build_completion_provider(**seams)
    tr = prov("pm-x", _hold(), armed=True, budget=_BUDGET, now=1000)
    # identical behavior
    assert tr["status"] == C.AWAITING_PROGRESS
    assert calls["promote"] == [("pm-x", "pm-x-g2")] and calls["retire"] == []
    # rows emitted for the seams the provider owns (start+end each)
    sections = {r["section"] for r in rows}
    assert {"s3_sample1", "grade", "promote", "auto_resume_inject"} <= sections
    assert all(r["rotation_id"] == f"pm-x:{L_SID}" for r in rows)
    # every started section closed
    starts = [r for r in rows if r["phase"] == "start"]
    ends = [r for r in rows if r["phase"] == "end"]
    assert len(starts) == len(ends)


def test_uninstrumented_matches_instrumented_status():
    plain, _ = _seams()
    inst, _ = _seams(rows=[])
    a = C.build_completion_provider(**plain)("pm-x", _hold(), armed=True,
                                             budget=_BUDGET, now=1000)
    b = C.build_completion_provider(**inst)("pm-x", _hold(), armed=True,
                                            budget=_BUDGET, now=1000)
    assert a["status"] == b["status"] == C.AWAITING_PROGRESS


def test_instrumented_effect_verify_beat_emits_effect_verify_section():
    rows = []
    seams, calls = _seams(rows=rows, effect_check_fn=lambda: True)
    prov = C.build_completion_provider(**seams)
    tr = prov("pm-x", _hold(promoted_at=1000, status=C.RETIRED_AWAITING_EFFECT),
              armed=True, budget=_BUDGET, now=1900)
    assert tr["status"] == C.COMPLETED       # predecessor already retired -> no retire here
    assert "effect_verify" in {r["section"] for r in rows}


def test_summarize_over_two_instrumented_beats():
    rows = []
    seams, _ = _seams(rows=rows)
    prov = C.build_completion_provider(**seams)
    prov("pm-x", _hold(), armed=True, budget=_BUDGET, now=1000)          # promote beat
    seams2, _ = _seams(rows=rows, effect_check_fn=lambda: True)
    C.build_completion_provider(**seams2)(
        "pm-x", _hold(promoted_at=1000), armed=True, budget=_BUDGET, now=2500)
    s = RT.summarize(rows)[f"pm-x:{L_SID}"]
    assert s["beats"] == 2
    assert s["inter_beat_gap_s"] >= 0
