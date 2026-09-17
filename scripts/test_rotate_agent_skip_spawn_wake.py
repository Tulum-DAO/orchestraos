"""RED-first: `rotate_agent --skip-spawn` must WAKE an idle successor and WAIT for a real
readback before grading (the skip-spawn wake fix).

Live failure, a live rotation, 2026-09-17: the spawn died before the init inject
(item 1), the driver retried with --skip-spawn against the already-open but never-woken pane. The
skip-spawn path wrote the q-keyed scaffold at 01:10:07Z and graded it 3 s later
(HOLD_GRADE 01:10:10Z, artifacts archived as .held-20260917T011010Z) — the successor had never
been asked anything. the driver recovered by hand-waking the pane and re-running --skip-spawn, which
correctly preserved the successor-authored readback.

Contract for --skip-spawn:
  * readback already COMPLETE on disk (the commit + retry recovery) -> grade as-is, no inject;
  * otherwise -> inject the readback prompt into the successor pane (the wake), poll for a
    successor-authored readback (non-scaffold, >=10 lines, q-keyed) with a bounded timeout,
    and on timeout HOLD (hold_readback_incomplete, artifacts preserved) — never grade the
    scaffold, never FAIL.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
import rotate_agent  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_orchestra(tmp_path):
    prior_dir = rotate_agent.set_orchestra_dir(tmp_path)
    prior_env = os.environ.pop("IDENTITY_STORE_CUTOVER", None)
    for sub in ("state", "logs", "state/agent-handoffs", "docs"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    try:
        yield tmp_path
    finally:
        rotate_agent.set_orchestra_dir(prior_dir)
        if prior_env is not None:
            os.environ["IDENTITY_STORE_CUTOVER"] = prior_env


def _wire(monkeypatch, tmp_path, *, readback_complete: bool, wait_result: bool):
    sandbox_registry = tmp_path / "registry.json"
    sandbox_registry.write_text(json.dumps({"agents": {
        "seat": {"name": "seat", "tier": "T2", "generation": 1,
                 "always_on": True, "runtime": "claude"}}}))
    monkeypatch.setattr(rotate_agent, "REGISTRY_PATH", sandbox_registry, raising=False)
    monkeypatch.setattr(rotate_agent, "get_agent_registry",
                        lambda seat: {"generation": 1, "runtime": "claude", "cwd": ".",
                                      "session_id": "sid-pred", "tier": "T2", "always_on": True},
                        raising=False)
    monkeypatch.setattr(rotate_agent, "run_cmd", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(rotate_agent, "commit_rotation_artifacts",
                        lambda alias, **k: {"ok": True, "sha": "stub", "reason": "", "retried": False},
                        raising=False)
    monkeypatch.setattr(rotate_agent, "ensure_handoff_artifact",
                        lambda *a, **k: rotate_agent.Path("/tmp/h.md"), raising=False)
    monkeypatch.setattr(rotate_agent, "ensure_canary_artifact", lambda *a, **k: None, raising=False)
    # the scaffold writer runs for real against the sandbox HANDOFFS_DIR (no canary => q1 stub)
    monkeypatch.setattr(rotate_agent, "extract_active_sid", lambda *a, **k: "sid-succ", raising=False)
    monkeypatch.setattr(rotate_agent, "_successor_sid_verified", lambda *a, **k: True, raising=False)
    monkeypatch.setattr(rotate_agent, "archive_attempt_artifacts", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(rotate_agent, "_readback_is_complete",
                        lambda *a, **k: readback_complete, raising=False)
    monkeypatch.setattr(rotate_agent, "_wait_for_complete_readback",
                        lambda *a, **k: wait_result, raising=False)

    events = []
    monkeypatch.setattr(rotate_agent, "drive_successor_readback",
                        lambda alias, seat, **k: (events.append(["DRIVE", alias, k.get("session")]), False)[1],
                        raising=False)
    monkeypatch.setattr(rotate_agent, "grade_successor_readback",
                        lambda alias, seat, **k: (events.append(["GRADE", alias]),
                                                  {"disposition": "FAIL", "artifact_written": True})[1],
                        raising=False)
    monkeypatch.setattr(rotate_agent.promoter, "promote",
                        lambda **kw: (events.append(["PROMOTE"]), {"registry_entry": {}, "warnings": []})[1],
                        raising=False)

    def fake_run(argv, *a, **k):
        if isinstance(argv, (list, tuple)):
            events.append(list(argv))

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""
        return _P()
    monkeypatch.setattr(subprocess, "run", fake_run)
    return sandbox_registry, events


def test_skip_spawn_idle_successor_is_woken_and_never_graded_on_the_scaffold(monkeypatch, tmp_path):
    """The live defect: no readback on disk + --skip-spawn => the successor must be driven
    (readback prompt injected into ITS pane) and, when nothing real arrives in the window,
    the run HOLDs as incomplete — the scaffold is never handed to the grader."""
    sandbox_registry, events = _wire(monkeypatch, tmp_path, readback_complete=False, wait_result=False)
    result = rotate_agent.execute_rotation("seat", skip_spawn=True)
    assert ["DRIVE", "seat-g2", "seat-g2"] in events, f"successor was never woken: {events}"
    assert not [e for e in events if e and e[0] == "GRADE"], f"scaffold was graded: {events}"
    assert result["status"] == "hold_readback_incomplete" and result["promoted"] is False
    reg = json.loads(sandbox_registry.read_text())
    assert "seat-g2" not in reg["agents"]            # provisional row rolled back
    # artifacts preserved (no archive) so the successor can finish and the driver can retry
    assert (tmp_path / "state" / "agent-handoffs" / "seat-g2.readback.md").exists()


def test_skip_spawn_grades_once_the_woken_successor_authors_a_real_readback(monkeypatch, tmp_path):
    _, events = _wire(monkeypatch, tmp_path, readback_complete=False, wait_result=True)
    rotate_agent.execute_rotation("seat", skip_spawn=True)
    order = [e[0] for e in events if e and e[0] in ("DRIVE", "GRADE")]
    assert order == ["DRIVE", "GRADE"], order


def test_skip_spawn_with_complete_readback_grades_as_is_without_a_wake(monkeypatch, tmp_path):
    """The commit + --skip-spawn recovery: a complete successor-authored readback on disk is
    graded directly; injecting a second readback prompt into a working seat is forbidden."""
    _, events = _wire(monkeypatch, tmp_path, readback_complete=True, wait_result=True)
    rotate_agent.execute_rotation("seat", skip_spawn=True)
    assert not [e for e in events if e and e[0] == "DRIVE"], events
    assert [e for e in events if e and e[0] == "GRADE"], events


def test_paragraph_style_readback_is_complete_not_a_stub(monkeypatch, tmp_path):
    """7 non-blank lines but 1500 chars of q-keyed answers must pass the pre-gate (the live
    e2e proof: a successor answered each question as one long line and sat in
    HOLD_READBACK_INCOMPLETE for 180 s although the grader would have PASSed it)."""
    monkeypatch.setattr(rotate_agent, "HANDOFFS_DIR", tmp_path, raising=False)
    canary = {"questions": [{"id": "q1", "question": "a"}, {"id": "q2", "question": "b"}, {"id": "q3", "question": "c"}]}
    long = "x" * 400
    (tmp_path / "s-g2.readback.md").write_text(f"# Readback\n\nq1\n\n{long}\n\nq2\n\n{long}\n\nq3\n\n{long}\n")
    assert rotate_agent._readback_is_complete("s-g2", canary) is True
    (tmp_path / "s-g2.readback.md").write_text("# Readback\n\nq1\n\nshort\n\nq2\n\nshort\n\nq3\n\nshort\n")
    assert rotate_agent._readback_is_complete("s-g2", canary) is False
