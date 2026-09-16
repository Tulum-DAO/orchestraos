"""Tests — build_completion_provider in completion_mode, A.3-rework 3-way dispatch
(DEC-1787808620 / re-congruence DEC-1787817982). Keyed on hold state:
  * promoted_at ABSENT              -> complete_held_rotation (promote + auto-resume,
    DEFER retire) -> AWAITING_PROGRESS.
  * promoted_at, status AWAITING_PROGRESS -> complete_progressing_watch.
  * status RETIRED_AWAITING_EFFECT  -> complete_effect_verify.
Hermetic sandbox (no promote/kill/shell).
"""
from scripts.lineage_daemon import complete as C

L_SID = "sess-successor-L"
PRED_SID = "sess-predecessor"
_EFFECT = {"kind": "commit", "target": "abc123def"}
_BUDGET = {"per_beat_slot": True, "hourly_remaining": 4, "cooldown_clear": True}


def _seams(**over):
    calls = {"promote": [], "retire": [], "resume": [], "assist": [], "escalate": []}
    d = dict(
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
        promote_fn=lambda c, alias: calls["promote"].append((c, alias)) or {"ok": True},
        retire_fn=lambda c: calls["retire"].append(c) or {"retired": True},
        completion_mode=True,
        first_effect_fn=lambda c: _EFFECT,
        auto_resume_fn=lambda c, a, eff: calls["resume"].append((c, a, eff)),
        progressing_fn=lambda s: True,
        context_assist_fn=lambda c, s: calls["assist"].append((c, s)),
        escalate_fn=lambda c, s, ctx: calls["escalate"].append(s),
        predecessor_live_fn=lambda c: True,
    )
    d.update(over)
    return d, calls


def _hold(**over):
    h = {"canary": "pm-x", "successor": "pm-x-g2",
         "status": "hold:successor-unconfirmed", "created_at": 0, "resolved": False}
    h.update(over)
    return h


def test_first_beat_promotes_defers_retire_auto_resumes():
    seams, calls = _seams()
    prov = C.build_completion_provider(**seams)
    tr = prov("pm-x", _hold(), armed=True, budget=_BUDGET, now=1000)
    assert tr["status"] == C.AWAITING_PROGRESS
    assert calls["promote"] == [("pm-x", "pm-x-g2")]
    assert calls["retire"] == []                          # NOT retired at promote
    assert calls["resume"] == [("pm-x", "pm-x-g2", _EFFECT)]


def test_progressing_watch_beat_progressing_retires():
    seams, calls = _seams(progressing_fn=lambda s: True)
    prov = C.build_completion_provider(**seams)
    tr = prov("pm-x", _hold(promoted_at=1000, status=C.AWAITING_PROGRESS),
              armed=True, budget=_BUDGET, now=1000 + 900)
    assert tr["status"] == C.RETIRED_AWAITING_EFFECT
    assert calls["retire"] == ["pm-x"] and calls["escalate"] == []


def test_progressing_watch_beat_struggling_assists():
    seams, calls = _seams(progressing_fn=lambda s: False)
    prov = C.build_completion_provider(**seams)
    tr = prov("pm-x", _hold(promoted_at=1000, status=C.AWAITING_PROGRESS),
              armed=True, budget=_BUDGET, now=1000 + 900)
    assert tr["status"] == C.AWAITING_PROGRESS
    assert calls["assist"] == [("pm-x", "pm-x-g2")]
    assert calls["retire"] == [] and calls["escalate"] == []


def test_progressing_watch_bound_retires_anyway_and_escalates():
    seams, calls = _seams(progressing_fn=lambda s: False)
    prov = C.build_completion_provider(**seams)
    tr = prov("pm-x", _hold(promoted_at=1000, status=C.AWAITING_PROGRESS),
              armed=True, budget=_BUDGET, now=1000 + 3 * 900)
    assert tr["status"] == C.RETIRED_ESCALATED
    assert calls["retire"] == ["pm-x"] and calls["escalate"] == ["pm-x-g2"]


def test_effect_watch_beat_present_completes():
    seams, calls = _seams(effect_check_fn=lambda: True)
    prov = C.build_completion_provider(**seams)
    tr = prov("pm-x", _hold(promoted_at=1000, status=C.RETIRED_AWAITING_EFFECT),
              armed=True, budget=_BUDGET, now=1000 + 900)
    assert tr["status"] == C.COMPLETED
    assert calls["escalate"] == []


def test_effect_watch_beat_timeout_escalates_keeps_running():
    seams, calls = _seams(effect_check_fn=lambda: False)
    prov = C.build_completion_provider(**seams)
    tr = prov("pm-x", _hold(promoted_at=1000, status=C.RETIRED_AWAITING_EFFECT),
              armed=True, budget=_BUDGET, now=1000 + 3 * 900)
    assert tr["status"] == C.ESCALATED
    assert calls["escalate"] == ["pm-x-g2"]
    assert calls["retire"] == []                          # successor keeps running


def test_retire_targets_predecessor_archive_row_and_repins_tmux(monkeypatch):
    """Verify _retire targets predecessor_archived row and performs tmux session rename."""
    retired_targets = []
    tmux_cmds = []

    monkeypatch.setattr(
        "scripts.lineage_daemon.executors.plan_retire",
        lambda target, armed=True, orchestra_dir=None: retired_targets.append(target) or {"retired": True, "target": target}
    )

    import subprocess
    def fake_subprocess_run(cmd, *args, **kwargs):
        tmux_cmds.append(cmd)
        class Dummy:
            returncode = 0
        return Dummy()

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    prov = C.build_completion_provider(
        orchestra_dir="/tmp/test-orch",
        registry={"agents": {"pm-x": {"agent_id": "pm-x"}, "pm-x-gen1": {"agent_id": "pm-x-gen1", "succeeded_by": "pm-x"}}},
        dry=False,
    )

    # Invoke _retire via promote_res report
    promote_res = {"ok": True, "report": {"predecessor_archived": "pm-x-gen1", "predecessors_marked": ["pm-x-gen1"]}}
    res = prov.retire("pm-x", "pm-x-g2", promote_res)

    assert "pm-x-gen1" in retired_targets
    assert ["tmux", "kill-session", "-t", "pm-x"] in tmux_cmds
    assert ["tmux", "rename-session", "-t", "pm-x-g2", "pm-x"] in tmux_cmds

