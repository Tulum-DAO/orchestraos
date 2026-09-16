"""E2E (DEC-1787861488): a completion beat promotes with ZERO override params.

The flagship's honest gap: the promote-half needed two manual free-text
overrides (--rotation-trigger-override + --shadow-skip-reason). This drives
the REAL promote_successor.promote through the provider's DEFAULT promote seam
over a scratch git orchestra:

  * G7      — NO detector file exists anywhere (the quiescent-predecessor
              shape); the gate is satisfied by the S1 hold-open snapshot
              re-read from the scratch fleet-beat-state.json store (C1/C2).
  * KEY-1   — no skip row is ever written; the gate is satisfied by the
              auto_strict_grade row the provider appends at grade time.
  * T4      — a real committed readback in the scratch git repo (no waiver).

Injected seams are confined to the daemon-side readers (live-sid, mtimes,
3-store coherence, confirm/safety/graduation/retire fakes); the promote path
itself — every guard — is the real verb, called with NO override params.
"""
import importlib
import json
import os
import subprocess
import sys
import time

from scripts.lineage_daemon import complete as C

_scripts = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)
from lineage_gate import shadow as SH                     # noqa: E402


PRED_SID = "sid-predecessor-0000"
SUCC_SID = "sid-successor-1111"
CANON = "worker-z"
SUCC = "worker-z-g5"
BUDGET = {"per_beat_slot": True, "hourly_remaining": 4, "cooldown_clear": True}


def _write_transcript(projects_dir, sid, declares, lines=14, span_s=120):
    d = projects_dir / "scratch-project"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    from datetime import datetime, timezone
    t0 = time.time() - span_s
    rows = [{"type": "user", "message": {"content": f"You are {declares} — init."},
             "timestamp": datetime.fromtimestamp(t0, timezone.utc).isoformat()}]
    for i in range(lines - 1):
        rows.append({"type": "assistant", "message": {"content": f"w{i}"},
                     "timestamp": datetime.fromtimestamp(
                         t0 + i + 1, timezone.utc).isoformat()})
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return p


def _git(orch, *args):
    return subprocess.run(["git", "-C", str(orch), *args],
                          capture_output=True, text=True)


def _seed(orch, projects):
    handoffs = orch / "state" / "agent-handoffs"
    handoffs.mkdir(parents=True)
    (orch / "state" / "agents").mkdir(parents=True)
    reg = {"agents": {
        CANON: {"tier": "T2", "tmux_session": CANON, "cwd": str(orch),
                "always_on": False, "name": "Worker Z", "status": "online",
                "generation": 4, "session_id": PRED_SID, "runtime": "claude",
                "resume_command": f"claude --resume {PRED_SID}"},
        SUCC: {"tier": "T2", "tmux_session": SUCC, "cwd": str(orch),
               "always_on": False, "name": "Worker Z g5", "runtime": "claude",
               "status": "provisioning", "session_id": None, "generation": 5,
               "handoff_from": CANON},
    }}
    (orch / "registry.json").write_text(json.dumps(reg))
    sess = {CANON: {"status": "online", "resumable": True, "session_id": PRED_SID,
                    "resume_command": f"claude --resume {PRED_SID}",
                    "tmux_session": CANON}}
    (orch / "state" / "agent-sessions.json").write_text(json.dumps(sess))
    _write_transcript(projects, PRED_SID, CANON)
    sp = _write_transcript(projects, SUCC_SID, SUCC)
    os.utime(sp, (time.time() + 60, time.time() + 60))

    # T4: a real, COMMITTED readback (no waiver anywhere in this test).
    rb = handoffs / f"{SUCC}.readback.md"
    rb.write_text("# readback\n" + "\n".join(
        f"- absorbed item {i}: real content line" for i in range(12)) + "\n")
    _git(orch, "init", "-q")
    _git(orch, "config", "user.email", "t@t")
    _git(orch, "config", "user.name", "t")
    _git(orch, "add", "state/agent-handoffs")
    _git(orch, "commit", "-q", "-m", "readback")

    # strict comprehension artifact (the grade's authority for the KEY-1 row).
    art = handoffs / f"{SUCC}.comprehension.json"
    art.write_text(json.dumps({"successor": SUCC, "mode": "strict",
                               "pass": True, "provenance_sid": SUCC_SID,
                               "per_question_scores": {"q1": 1.0}}))
    return art


def _hold_with_evidence(orch):
    """S1 hold-open snapshot + the hold row, persisted to the STORE G7
    re-reads (C1). No detector file is ever written."""
    snapshot_at = time.time() - 1800          # hold opened 30min ago
    ev = {"used_pct": 86, "detector_ts": snapshot_at - 60,
          "detector_path": f"/tmp/claude-ctx-{PRED_SID}.json",
          "snapshot_at": snapshot_at, "predecessor_sid": PRED_SID}
    hold = {"canary": CANON, "successor": SUCC,
            "status": "hold:successor-unconfirmed",
            "reason": "hold:successor-unconfirmed",
            "created_at": snapshot_at, "escalation_target": "gm",
            "repaged_at": None, "resolved": False, "trigger_evidence": ev}
    state = {"locks": {"rotations": []}, "ledger": {"holds": [hold]},
             "history": {"retires": []}}
    (orch / "state" / "fleet-beat-state.json").write_text(json.dumps(state))
    return hold


def test_completion_beat_promotes_with_zero_override_params(
        tmp_path, monkeypatch):
    orch = tmp_path / "orch"
    projects = tmp_path / "projects"
    art = _seed(orch, projects)
    hold = _hold_with_evidence(orch)
    ledger = orch / "state" / "lineage-gate-shadow.jsonl"

    # fresh promote module bound to the scratch ORCHESTRA_DIR — the SAME
    # module object the provider's default seam imports. PIN the import to
    # THIS code tree first: other tests in the session can leave the LIVE
    # orchestra repo on sys.path ahead of this tree, and `scripts` is a
    # namespace package — without the prepend, the reimport can silently load
    # the LIVE repo's promote_successor (no trigger_evidence param) and this
    # test would flake by ordering. The __file__ assert makes that loud.
    monkeypatch.setenv("ORCHESTRA_DIR", str(orch))
    monkeypatch.syspath_prepend(os.path.dirname(_scripts))
    sys.modules.pop("scripts.promote_successor", None)
    SPS = importlib.import_module("scripts.promote_successor")
    assert os.path.dirname(os.path.abspath(SPS.__file__)) == _scripts, \
        f"promote_successor resolved OUTSIDE this code tree: {SPS.__file__}"
    monkeypatch.setattr(SPS, "_cwd_to_project_dir",
                        lambda cwd: projects / "scratch-project" if cwd else None)
    SI = importlib.import_module("sid_invariants")
    monkeypatch.setattr(SI, "PROJECTS_ROOT", projects)
    # the KEY-1 gate's default ledger -> the scratch ledger the provider writes.
    monkeypatch.setattr(SH, "LEDGER", ledger)

    retired = []
    prov = C.build_completion_provider(
        orchestra_dir=str(orch), shadow_ledger=ledger,
        live_sid_fn=lambda a: SUCC_SID if a == SUCC else PRED_SID,
        l_start_time_fn=lambda sid: time.time() - 3600,
        readback_mtime_fn=lambda a: time.time() - 60,
        comprehension_mtime_fn=lambda a: time.time() - 50,
        provenance_sid_fn=lambda a: None,
        canonical_store_sids_fn=lambda c: (PRED_SID, PRED_SID, PRED_SID),
        confirm_fn=lambda c, s: {"outcome": "confirmed"},
        grade_fn=lambda c, s, sid: {"disposition": "PASS", "strict": True,
                                    "artifact": str(art),
                                    "artifact_written": True},
        safety_fn=lambda c: ("SUPERSEDED_SAFE", "ok"),
        graduation_fn=lambda alias: True,
        retire_fn=lambda c: retired.append(c) or {"retired": True},
    )   # promote_fn=None -> the REAL promote_successor.promote

    tr = prov(CANON, hold, armed=True, budget=BUDGET, now=time.time())

    if tr["status"] != C.COMPLETED:
        # surface the REAL refusal (the provider swallows PromotionRefused
        # into ok=False) so a failure names its gate instead of a bare status.
        SPS.promote(CANON, SUCC, session_id=SUCC_SID,
                    trigger_evidence=hold["trigger_evidence"])
    assert tr["status"] == C.COMPLETED, tr
    assert tr["promoted"] is True and retired == [CANON]
    # the REAL canonical swap happened: registry now maps CANON -> SUCC_SID.
    reg = json.loads((orch / "registry.json").read_text())
    assert reg["agents"][CANON]["session_id"] == SUCC_SID
    # KEY-1: an honest auto row, and NO skip row anywhere.
    rows = SH.read_rows(ledger)
    kinds = [r.get("kind") for r in rows]
    assert "auto_strict_grade" in kinds and "skip" not in kinds
    # G7 rode the store-validated hold-open evidence (no detector existed).
    assert not os.path.exists(f"/tmp/claude-ctx-{PRED_SID}.json")
