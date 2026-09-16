"""rotation_progress.py — the retire-trigger progress signal (DEC-1787808620 A.3 rework,
gm-LOCKED msg_772da5ec; re-congruence DEC-1787817982 CONSENSUS).

`progressing(successor)` is the SOLE gate before the predecessor is destroyed, so it
keys on a POSITIVE task-directed ARTIFACT — a focus-file edit / commit-delta /
working-tree change on the lineage's actual work-files — and NEVER on activity volume
(tool-call / read counts). A flailing successor racks up reads + tool-calls without
producing any work artifact; that is exactly the "still struggling" state (keeps the
predecessor alive + fires context-assist), NOT progress. There is deliberately no
tool-call/activity-count parameter, so volume cannot be mistaken for progress by
construction.
"""
import os
import shlex
import subprocess

_TIMEOUT_S = 10


def _run_rc0(argv, cwd=None) -> bool:
    try:
        return subprocess.run(argv, cwd=cwd, timeout=_TIMEOUT_S,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _default_focus_edited(successor) -> bool:
    """Best-effort real default: a working-tree change on the lineage's focus files.
    Overridden by the caller (the completion provider passes a seam bound to the
    lineage's repo + focus roots). Returns False on any absence/error (fail toward
    'not progressing' -> the predecessor is kept alive, never destroyed on a false
    positive)."""
    return False


def _default_commit_delta(successor) -> int:
    """Best-effort real default: new commits on the successor's work branch since the
    promote baseline. Overridden by the caller. 0 on any absence/error (fail toward
    'not progressing')."""
    return 0


def progressing(successor, *, focus_edited_since_fn=None, commit_delta_fn=None) -> bool:
    """True IFF the successor produced a POSITIVE task-directed ARTIFACT since the
    promote — a focus-file working-tree edit OR a commit-delta. Artifact-only: there is
    NO tool-call/activity-count input, so a busy-but-empty (flailing) successor is NOT
    progressing. Fail-closed toward NOT-progressing (keeps the predecessor alive rather
    than destroying it on a false positive)."""
    fe = focus_edited_since_fn or _default_focus_edited
    cd = commit_delta_fn or _default_commit_delta
    if fe(successor):
        return True
    try:
        return (cd(successor) or 0) > 0
    except Exception:  # noqa: BLE001 -- any error => not progressing (fail-closed)
        return False


def focus_worktree_edited(cwd, focus_roots) -> bool:
    """Real signal for `focus_edited_since_fn`: `git status --porcelain` shows a change
    under one of the lineage's focus roots. Pure-ish (shells git read-only)."""
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=cwd,
                             timeout=_TIMEOUT_S, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True)
    except Exception:  # noqa: BLE001
        return False
    if out.returncode != 0:
        return False
    roots = [r.rstrip("/") for r in (focus_roots or []) if r]
    for line in out.stdout.splitlines():
        path = line[3:].strip() if len(line) > 3 else ""
        if path and (not roots or any(path == r or path.startswith(r + "/")
                                      for r in roots)):
            return True
    return False


def commit_delta_since(cwd, baseline_sha) -> int:
    """Real signal for `commit_delta_fn`: count of commits on HEAD after `baseline_sha`
    (the promote-baseline). 0 on any error."""
    if not baseline_sha:
        return 0
    try:
        out = subprocess.run(["git", "rev-list", "--count",
                              f"{shlex.quote(baseline_sha)}..HEAD"], cwd=cwd,
                             timeout=_TIMEOUT_S, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True)
        return int(out.stdout.strip() or 0) if out.returncode == 0 else 0
    except Exception:  # noqa: BLE001
        return 0
