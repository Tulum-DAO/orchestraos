"""completion_arm_seams.py — the REAL seams that bind the A.3 completion recovery model
to a live lineage's work repo (ARM-PREP, DEC-1787808620). Replaces the fail-closed
module-defaults with genuine detection / surfacing for the armed lineage (ios-watch-dev,
repo /home/testuser/repos/watch-approval-app). INERT-until-armed is unchanged — these only
run for an armed lineage; arming is the allowlist add (owner: orchestra-builder, after
gm's arm-prep PASS).

Three builders, each returning a seam matching build_completion_provider's contract:
  * build_progressing_fn(repo, focus_roots, baseline_sha) -> progressing_fn(successor)
  * build_escalate_fn(...)        -> escalate_fn(canary, successor, ctx)
  * build_context_assist_fn(...)  -> context_assist_fn(canary, successor)
All keep the gm-LOCKED invariants: progressing keys on a POSITIVE task ARTIFACT only
(no activity/count), escalation is unmissable + first-escalation human-gated + notify-
failure-never-proceeds, context-assist is best-effort + dry-safe.
"""
import json
import os


def resolve_repo(orchestra_dir, agent_id):
    """The lineage's work repo = registry.agents[agent_id].cwd (all ios-watch-dev
    generations point at /home/testuser/repos/watch-approval-app). None if absent."""
    try:
        with open(os.path.join(orchestra_dir, "registry.json")) as f:
            reg = json.load(f)
        cwd = ((reg.get("agents") or {}).get(agent_id) or {}).get("cwd")
        return cwd if cwd and os.path.isdir(cwd) else None
    except Exception:  # noqa: BLE001
        return None


def build_progressing_fn(repo, focus_roots=None, baseline_sha=None):
    """A REAL progressing_fn(successor) for `repo`: POSITIVE task-directed ARTIFACT =
    a focus-file working-tree EDIT (uncommitted change under focus_roots) OR a
    commit-delta since the promote baseline. Rejects flailing/idle (reads+tool-calls,
    no artifact) by construction — it inspects the REPO, never activity. No repo =>
    fail-closed to NOT-progressing (predecessor kept alive)."""
    from scripts.lineage_daemon.rotation_progress import (
        progressing, focus_worktree_edited, commit_delta_since)

    def progressing_fn(successor):
        if not repo:
            return False
        return progressing(
            successor,
            focus_edited_since_fn=lambda s: focus_worktree_edited(repo, focus_roots),
            commit_delta_fn=lambda s: commit_delta_since(repo, baseline_sha))
    return progressing_fn


def repo_head(repo):
    """The current HEAD sha of `repo` (the promote baseline for commit-delta). None on
    any error."""
    import subprocess
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, timeout=10,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def build_escalate_fn(orchestra_dir, *, dry=False, runtime_dir=None, request_fn=None):
    """A REAL escalate_fn(canary, successor, ctx): fire a genuinely UNMISSABLE the operator card
    (approvals-surface + watch) via approval.py request, gated by the first-escalation
    human-gate (once-sentinel + per-lineage escalation_armed). A notify (card) failure
    NEVER flips proceed. `request_fn(argv)` is injectable (tests pass a fake surface)."""
    from scripts.lineage_daemon.first_escalation_gate import first_escalation_gate

    def _fire_card(canary, successor, ctx):
        reason = str((ctx or {}).get("reason") or "no-progress")
        phase = ("pre-retire never-progressed" if "never-progress" in reason
                 else "post-retire no-effect")
        question = (f"Lineage {canary} successor {successor}: {phase} — look. "
                    f"Rotation completion needs a human; nothing auto-reverts.")
        argv = ["python3", os.path.join(orchestra_dir, "scripts", "approval.py"),
                "request", question, "--from", "lineage-daemon",
                "--worker-kind", "pane", "--risk", "high",
                "--reversibility", "hard", "--feature", "Rotation",
                "--options", "acknowledge,investigate",
                "--thread-key", f"lineage-escalation-{canary}"]
        if request_fn is not None:
            return request_fn(argv)
        if dry:
            return None
        import subprocess
        subprocess.run(argv, capture_output=True, timeout=30)

    def escalate_fn(canary, successor, ctx):
        def _notify(root, c):
            _fire_card(canary, successor, ctx)      # raises on failure -> gate HOLDS
        return first_escalation_gate(canary, runtime_dir=runtime_dir,
                                     notify_fn=_notify, ctx=ctx)
    return escalate_fn


def build_context_assist_fn(orchestra_dir, *, dry=False, send_fn=None):
    """A REAL context_assist_fn(canary, successor): inject an enrichment request to the
    still-alive predecessor (the canonical `canary` seat) asking it to strengthen the
    struggling successor's understanding. Best-effort + dry-safe. `send_fn(canary,
    subject, body)` injectable for tests."""
    def context_assist_fn(canary, successor):
        subject = "[LINEAGE ASSIST] your successor is struggling — inject more context"
        body = (f"Your successor {successor} has not yet made task-directed progress in "
                f"the work repo. Before you retire, inject ADDITIONAL context (the deep "
                f"specifics it still needs — file locations, the exact next change, the "
                f"gotchas) to strengthen its understanding.")
        if send_fn is not None:
            return send_fn(canary, subject, body)
        if dry:
            return None
        try:
            from msg_store import MessageStore
            MessageStore().send(
                from_agent="lineage-daemon", to_agent=canary,
                type="lineage_context_assist", priority="high",
                subject=subject, body=body, source="fleet-beat")
        except Exception:  # noqa: BLE001 -- best-effort; never breaks the beat
            return None
    return context_assist_fn
