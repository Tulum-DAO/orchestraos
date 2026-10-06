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
    # TARGET-AWARE fake. The previous version returned rc=0 for EVERY tmux call, which made
    # the unconditional bare `kill-session -t pm-x` look correct. Real tmux resolves a bare
    # target by PREFIX, so with the bare `pm-x` ABSENT that kill reached `pm-x-g2` -- the
    # successor being consolidated. This models the live shape: bare name absent, alias present.
    live = {"pm-x-g2": "4242"}

    def fake_subprocess_run(cmd, *args, **kwargs):
        tmux_cmds.append(cmd)
        target = cmd[cmd.index("-t") + 1] if "-t" in cmd else ""
        name = (target[1:] if target.startswith("=") else target).split(":")[0]
        out, rc = "", 0
        if "has-session" in cmd:
            rc = 0 if name in live else 1
        elif "display-message" in cmd:
            out = live.get(name, "")
        elif "rename-session" in cmd:
            if name in live:
                live[cmd[-1]] = live.pop(name)
            else:
                rc = 1
        elif "kill-session" in cmd:
            rc = 0 if live.pop(name, None) is not None else 1

        class Dummy:
            returncode = rc
            stdout = out
            stderr = ""
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
    # THE RENAME is this path's intent, and it must use an EXACT target.
    assert ["tmux", "rename-session", "-t", "=pm-x-g2", "pm-x"] in tmux_cmds
    assert res.get("consolidated") is True, res
    # NO KILL AT ALL in this shape. The old assertion REQUIRED
    # `kill-session -t pm-x` (bare, unconditional); with the bare canonical name absent that
    # prefix-matched `pm-x-g2` and destroyed the successor being consolidated. There is
    # nothing to kill here, so killing is the defect, not the contract.
    assert not [c for c in tmux_cmds if "kill-session" in c], tmux_cmds
    # NO BARE TARGETS ANYWHERE.
    bare = [c for c in tmux_cmds
            if "-t" in c and not c[c.index("-t") + 1].startswith("=")]
    assert not bare, f"bare prefix-matching tmux target(s): {bare}"
    # The successor now holds the canonical name.
    assert "pm-x" in live and "pm-x-g2" not in live, live


def test_retire_KILLS_a_stranger_holding_the_canonical_name(monkeypatch):
    """The kill must still happen when the bare name is held by something that is NOT the
    alias -- otherwise the rename target is occupied and consolidation fails. This is the
    CONTROL proving the new condition did not simply disable the kill."""
    live = {"pm-x": "999", "pm-x-g2": "4242"}      # a stranger squats the canonical name
    killed = []
    monkeypatch.setattr(
        "scripts.lineage_daemon.executors.plan_retire",
        lambda target, armed=True, orchestra_dir=None: {"retired": True, "target": target})
    import subprocess

    def fake_run(cmd, *a, **kw):
        target = cmd[cmd.index("-t") + 1] if "-t" in cmd else ""
        name = (target[1:] if target.startswith("=") else target).split(":")[0]
        out, rc = "", 0
        if "has-session" in cmd:
            rc = 0 if name in live else 1
        elif "display-message" in cmd:
            out = live.get(name, "")
        elif "kill-session" in cmd:
            if live.pop(name, None) is not None:
                killed.append(name)
            else:
                rc = 1
        elif "rename-session" in cmd:
            if name in live:
                live[cmd[-1]] = live.pop(name)
            else:
                rc = 1

        class D:
            returncode = rc
            stdout = out
            stderr = ""
        return D()
    monkeypatch.setattr(subprocess, "run", fake_run)

    prov = C.build_completion_provider(orchestra_dir="/tmp/test-orch", registry={"agents": {}},
                                       dry=False)
    res = prov.retire("pm-x", "pm-x-g2",
                      {"ok": True, "report": {"predecessor_archived": "pm-x-gen1"}})
    assert killed == ["pm-x"], f"the stranger at the canonical name must be killed: {killed}"
    assert res.get("consolidated") is True, res
    assert live.get("pm-x") == "4242", "the successor should now hold the canonical name"


def test_retire_does_NOT_kill_when_promote_ALREADY_consolidated(monkeypatch):
    """If the canonical name is already held by the alias's own pane, promote has run and
    there is nothing to do. An EXACT but still-unconditional kill would destroy the
    just-promoted successor here, which is why exactness alone was not the fix."""
    # ALREADY-CONSOLIDATED state: the successor has been renamed onto the canonical name,
    # so the ALIAS NO LONGER EXISTS. An earlier version of this fake returned a pane pid for
    # `pm-x-g2` unconditionally, even though `pm-x-g2` is absent from its own `live` dict --
    # an UNFAITHFUL MOCK that made a dead code path look implemented. Real tmux: a renamed
    # session stops answering to its old name (measured: `display-message -t =alias:0.0`
    # returns EMPTY after the rename).
    live = {"pm-x": "4242"}
    killed = []
    monkeypatch.setattr(
        "scripts.lineage_daemon.executors.plan_retire",
        lambda target, armed=True, orchestra_dir=None: {"retired": True, "target": target})
    import subprocess

    def fake_run(cmd, *a, **kw):
        target = cmd[cmd.index("-t") + 1] if "-t" in cmd else ""
        name = (target[1:] if target.startswith("=") else target).split(":")[0]
        out, rc = "", 0
        if "has-session" in cmd:
            rc = 0 if name in live else 1
        elif "display-message" in cmd:
            # honours `live`: an absent exact target yields rc 0 with EMPTY stdout
            out = live.get(name, "")
        elif "kill-session" in cmd:
            if live.pop(name, None) is not None:
                killed.append(name)
            else:
                rc = 1
        elif "rename-session" in cmd:
            if name in live:
                live[cmd[-1]] = live.pop(name)
            else:
                rc = 1

        class D:
            returncode = rc
            stdout = out
            stderr = ""
        return D()
    monkeypatch.setattr(subprocess, "run", fake_run)

    prov = C.build_completion_provider(orchestra_dir="/tmp/test-orch", registry={"agents": {}},
                                       dry=False)
    res = prov.retire("pm-x", "pm-x-g2",
                      {"ok": True, "report": {"predecessor_archived": "pm-x-gen1"}})
    assert killed == [], f"killed the already-promoted successor: {killed}"
    assert res.get("already") is True and res.get("consolidated") is True, res
    assert live == {"pm-x": "4242"}, f"the seat must be left intact: {live}"


def test_retire_reports_a_FAILED_consolidation_instead_of_claiming_success(monkeypatch):
    """Both return codes used to be DISCARDED and `{"retired": True}` returned regardless, so
    a failed consolidation reported retirement success."""
    monkeypatch.setattr(
        "scripts.lineage_daemon.executors.plan_retire",
        lambda target, armed=True, orchestra_dir=None: {"retired": True, "target": target})
    import subprocess

    def fake_run(cmd, *a, **kw):
        class D:
            returncode = 1          # neither the canonical name nor the alias exists
            stdout = ""
            stderr = "can't find session"
        return D()
    monkeypatch.setattr(subprocess, "run", fake_run)

    prov = C.build_completion_provider(orchestra_dir="/tmp/test-orch", registry={"agents": {}},
                                       dry=False)
    res = prov.retire("pm-x", "pm-x-g2",
                      {"ok": True, "report": {"predecessor_archived": "pm-x-gen1"}})
    assert res.get("consolidated") is False, res
    assert "consolidate_error" in res, res

