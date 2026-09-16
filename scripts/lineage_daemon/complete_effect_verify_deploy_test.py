"""Effect-verify is GENERAL over deploy topology (orchestra-builder msg_4558e2c9).

HOLE-2 post-promote effect-verify must key off the first_effect's DECLARED check
predicate, NOT a hardcoded branch/deploy topology. The Acme deploy rule (feature
agents commit to a branch; a merge agent FFs to main + cleans up the branch) is
ACME-ONLY; OrchestraOS/ios-watch-dev (our proving lineage) uses a direct commit on
the working branch. Both must resolve to "effect present -> NO rollback" by effect.

The mechanism: complete_effect_verify takes an injected effect_check_fn; the provider
default is check_effect(first_effect), which dispatches on the declared kind/check with
no topology assumption. For kind=commit that is `git cat-file -e <sha>` = OBJECT
existence, which survives an FF-merge-then-branch-delete (FF preserves the sha, still
reachable from main). These tests prove that by effect with real git.
"""
import shutil
import subprocess

import pytest

from scripts.focus_registry.effects import check_effect
from scripts.lineage_daemon import complete as C

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git absent")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True)


def _repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    _git(d, "commit", "-q", "--allow-empty", "-m", "root")
    return d


def _commit_on_branch(d, branch, msg):
    _git(d, "checkout", "-q", "-b", branch)
    (d / f"{branch}.txt").write_text(msg)
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", msg)
    return _git(d, "rev-parse", "HEAD").stdout.strip()


# --- OrchestraOS / ios-watch-dev: direct commit on the working branch (MUST work) ---

def test_orchestraos_direct_commit_effect_present(tmp_path):
    d = _repo(tmp_path)
    sha = _commit_on_branch(d, "work", "the fix")
    r = check_effect({"kind": "commit", "target": sha}, cwd=str(d))
    assert r["exists"] is True


# --- Acme: branch commit FF-merged to main + branch cleaned up before verify -----

def test_acme_branch_commit_survives_ff_merge_and_cleanup(tmp_path):
    d = _repo(tmp_path)
    sha = _commit_on_branch(d, "fix-x", "acme fix")
    # merge agent FFs main to the branch tip, then deletes the feature branch
    _git(d, "checkout", "-q", "master") if _has(d, "master") else _git(d, "checkout", "-q", "main")
    _git(d, "merge", "-q", "--ff-only", "fix-x")
    _git(d, "branch", "-q", "-D", "fix-x")
    # the declared first_effect sha is GONE as a branch but STILL exists (reachable
    # from main via the fast-forward) -> effect present, no false-negative rollback.
    r = check_effect({"kind": "commit", "target": sha}, cwd=str(d))
    assert r["exists"] is True


def _has(d, branch):
    return subprocess.run(["git", "rev-parse", "--verify", branch], cwd=d,
                          capture_output=True).returncode == 0


# --- a genuinely-absent effect is False (the real rollback trigger) -----------------

def test_absent_commit_effect_is_false(tmp_path):
    d = _repo(tmp_path)
    r = check_effect({"kind": "commit", "target": "deadbeefcafe0000"}, cwd=str(d))
    assert r["exists"] is False


# --- kind=command: a content/behavior predicate is topology-agnostic by construction -

def test_command_check_predicate_is_general(tmp_path):
    d = _repo(tmp_path)
    _commit_on_branch(d, "work", "landed content marker")
    # a declared check that tests CONTENT (grep the landed file) rather than a sha
    eff = {"kind": "command", "target": "content",
           "check": f"grep -q marker {d}/work.txt"}
    assert check_effect(eff, cwd=str(d))["exists"] is True


# --- end-to-end: effect present past the window -> COMPLETED, never a false escalate ----

def test_present_effect_completes_even_past_window_no_escalate(tmp_path):
    d = _repo(tmp_path)
    sha = _commit_on_branch(d, "fix-x", "acme fix")
    _git(d, "checkout", "-q", "master") if _has(d, "master") else _git(d, "checkout", "-q", "main")
    _git(d, "merge", "-q", "--ff-only", "fix-x")
    _git(d, "branch", "-q", "-D", "fix-x")

    escalated = []
    tr = C.complete_effect_verify(
        "pm-x", "pm-x-g2",
        {"promoted_at": 1000, "status": C.RETIRED_AWAITING_EFFECT},
        now=1000 + 99 * 900,                              # WAY past N=2 beats
        effect_check_fn=lambda: check_effect(
            {"kind": "commit", "target": sha}, cwd=str(d))["exists"],
        escalate_fn=lambda c, s, ctx: escalated.append(s))
    assert tr["status"] == C.COMPLETED               # effect present short-circuits window
    assert escalated == []                            # NO false escalation (deploy-general)
