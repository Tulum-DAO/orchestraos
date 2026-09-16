"""Tests — the REAL arm-prep seams for ios-watch-dev (DEC-1787808620 ARM-PREP).

progressing on real git; escalate against a fake surface; context-assist against a fake
send. Each in isolation.
"""
import shutil
import subprocess

import pytest

from scripts.lineage_daemon import completion_arm_seams as A

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git absent")


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True)


def _repo(tmp_path):
    d = tmp_path / "watch-approval-app"
    d.mkdir()
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    (d / "App.swift").write_text("import SwiftUI\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "baseline")
    return d


# --- progressing_fn: POSITIVE artifact only ------------------------------------

def test_progressing_true_on_focus_file_worktree_edit(tmp_path):
    d = _repo(tmp_path)
    (d / "App.swift").write_text("import SwiftUI\n// the successor's real edit\n")  # uncommitted
    fn = A.build_progressing_fn(str(d), focus_roots=None, baseline_sha=None)
    assert fn("ios-watch-dev-g2") is True


def test_progressing_true_on_commit_delta_since_baseline(tmp_path):
    d = _repo(tmp_path)
    base = A.repo_head(str(d))
    (d / "Feature.swift").write_text("struct Feature {}\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "successor work")
    fn = A.build_progressing_fn(str(d), focus_roots=None, baseline_sha=base)
    assert fn("ios-watch-dev-g2") is True


def test_progressing_false_on_clean_tree_no_new_commit_flailing(tmp_path):
    """A flailing/idle successor: read the repo, ran tool-calls, but produced NO
    artifact (clean tree, no commit past baseline) -> NOT progressing."""
    d = _repo(tmp_path)
    base = A.repo_head(str(d))
    fn = A.build_progressing_fn(str(d), focus_roots=None, baseline_sha=base)
    assert fn("ios-watch-dev-g2") is False


def test_progressing_false_when_edit_outside_focus_roots(tmp_path):
    d = _repo(tmp_path)
    (d / "README.md").write_text("docs only, not the focus work files\n")
    fn = A.build_progressing_fn(str(d), focus_roots=["Sources/"], baseline_sha=None)
    assert fn("ios-watch-dev-g2") is False       # edit not under the focus roots


def test_progressing_false_no_repo():
    fn = A.build_progressing_fn(None, focus_roots=None, baseline_sha=None)
    assert fn("ios-watch-dev-g2") is False       # fail-closed


def test_resolve_repo_from_registry(tmp_path):
    (tmp_path / "registry.json").write_text(
        '{"agents": {"ios-watch-dev": {"cwd": "%s"}}}' % str(tmp_path))
    assert A.resolve_repo(str(tmp_path), "ios-watch-dev") == str(tmp_path)


# --- escalate_fn: unmissable card via approval.py, first-escalation gated -------

def test_escalate_fires_real_card_through_first_escalation_gate(tmp_path):
    fired = []
    esc = A.build_escalate_fn("/orch", runtime_dir=str(tmp_path),
                              request_fn=lambda argv: fired.append(argv))
    r = esc("ios-watch-dev", "ios-watch-dev-g2",
            {"reason": "never-progressed-after-context-assist"})
    assert r["proceed"] is False and r["reason"] == "first-escalation-human-gate"
    assert len(fired) == 1
    argv = fired[0]
    assert "request" in argv and "approval.py" in " ".join(argv)
    assert any("pre-retire never-progressed" in a for a in argv)
    assert "--risk" in argv and "high" in argv


def test_escalate_second_holds_without_refiring(tmp_path):
    fired = []
    esc = A.build_escalate_fn("/orch", runtime_dir=str(tmp_path),
                              request_fn=lambda argv: fired.append(argv))
    esc("ios-watch-dev", "ios-watch-dev-g2", {"reason": "no-first-effect"})
    r2 = esc("ios-watch-dev", "ios-watch-dev-g2", {"reason": "no-first-effect"})
    assert r2["reason"] == "awaiting-human-clear"
    assert len(fired) == 1                        # NOT re-fired (once-sentinel)


def test_escalate_card_failure_never_proceeds(tmp_path):
    def boom(argv):
        raise RuntimeError("surface down")
    esc = A.build_escalate_fn("/orch", runtime_dir=str(tmp_path), request_fn=boom)
    r = esc("ios-watch-dev", "ios-watch-dev-g2", {"reason": "x"})
    assert r["proceed"] is False                  # notify failure -> HOLD, never proceed


def test_escalate_post_retire_phrasing(tmp_path):
    fired = []
    esc = A.build_escalate_fn("/orch", runtime_dir=str(tmp_path),
                              request_fn=lambda argv: fired.append(argv))
    esc("ios-watch-dev", "ios-watch-dev-g2", {"reason": "no-first-effect-within-window"})
    assert any("post-retire no-effect" in a for a in fired[0])


# --- context_assist_fn: real inject to the predecessor -------------------------

def test_context_assist_injects_to_predecessor(tmp_path):
    sent = []
    ca = A.build_context_assist_fn("/orch",
                                   send_fn=lambda c, subj, body: sent.append((c, subj)))
    ca("ios-watch-dev", "ios-watch-dev-g2")
    assert sent and sent[0][0] == "ios-watch-dev"   # routed to the LIVE predecessor seat
    assert "ASSIST" in sent[0][1]


# --- provider wiring: real default progressing resolves repo + detects edit ---------

def _reg_repo(tmp_path):
    """A tmp orchestra dir with a registry pointing ios-watch-dev at a real git repo."""
    import json
    repo = _repo(tmp_path)
    orch = tmp_path / "orch"
    (orch / "state" / "agent-handoffs").mkdir(parents=True)
    (orch / "registry.json").write_text(json.dumps({"agents": {
        "ios-watch-dev": {"cwd": str(repo), "lineage_root": "ios-watch-dev"}}}))
    return str(orch), repo


def test_provider_default_progressing_detects_real_edit(tmp_path):
    from scripts.lineage_daemon import complete as C
    orch, repo = _reg_repo(tmp_path)
    (repo / "App.swift").write_text("import SwiftUI\n// successor edit\n")   # uncommitted artifact
    prov = C.build_completion_provider(
        orchestra_dir=orch, completion_mode=True,
        # inject only the retire seam to observe; progressing uses the REAL default
        retire_fn=lambda c: None,
        escalate_fn=lambda c, s, ctx: None, context_assist_fn=lambda c, s: None,
        predecessor_live_fn=lambda c: True)
    hold = {"canary": "ios-watch-dev", "successor": "ios-watch-dev-g2",
            "status": C.AWAITING_PROGRESS, "promoted_at": 1000, "resolved": False}
    tr = prov("ios-watch-dev", hold, armed=True,
              budget={"per_beat_slot": True, "hourly_remaining": 4,
                      "cooldown_clear": True}, now=1000 + 900)
    assert tr["status"] == C.RETIRED_AWAITING_EFFECT       # real edit -> progressing -> retire


def test_provider_default_progressing_false_on_clean_repo(tmp_path):
    from scripts.lineage_daemon import complete as C
    orch, repo = _reg_repo(tmp_path)                       # clean tree, no new commit
    assists = []
    prov = C.build_completion_provider(
        orchestra_dir=orch, completion_mode=True, retire_fn=lambda c: None,
        escalate_fn=lambda c, s, ctx: None,
        context_assist_fn=lambda c, s: assists.append(s),
        predecessor_live_fn=lambda c: True)
    hold = {"canary": "ios-watch-dev", "successor": "ios-watch-dev-g2",
            "status": C.AWAITING_PROGRESS, "promoted_at": 1000, "resolved": False}
    tr = prov("ios-watch-dev", hold, armed=True,
              budget={"per_beat_slot": True, "hourly_remaining": 4,
                      "cooldown_clear": True}, now=1000 + 900)      # within bound
    assert tr["status"] == C.AWAITING_PROGRESS            # no artifact -> struggling
    assert assists == ["ios-watch-dev-g2"]               # context-assist fired
