#!/usr/bin/env python3
"""Lineage daemon entry -- OBSERVE-ONLY scaffold.

This CLI arms NOTHING. It is the observe-only shell for the context-
exhaustion lineage trigger (Problem B of docs/superpowers/specs/
2026-08-12-orchestra-registry-and-lineage-daemon.md). Its decision core
lives in scripts/lineage_daemon/decide.py and is exercised purely via
plan(); no fleet is read, no action is taken.

The later, separately-reviewed pass -- gated on a clean dry-run AND the operator's
explicit go -- will:
  * wire real fleet collection (status bars / jsonl tokens / death signals),
  * reuse the EXISTING helpers ONLY once armed:
      - scripts/park-idle.py            retire() (predecessor kill)
      - scripts/registry-update.py      registry lineage update
      - spawn-agent.sh                  successor spawn
  * gate every kill/spawn/cron behind one-tap human approval for T0/T1.

Until then this module does no tmux, no kill/spawn, no cron, and makes no
calls to any live service. main() is import-safe.
"""

import argparse
import json
import os
import re
import subprocess
import sys

# Put the worktree root on sys.path so `from scripts.lineage_daemon...`
# resolves both when run directly and when loaded via importlib.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# Runtime STATE (registry.json / agent-sessions.json) is LIVE, not code: a
# worktree checkout carries a stale snapshot, so observing from it leaves the
# jsonl/live-sid fallback blind. Read state from the canonical live dir
# (READ-ONLY -- never written here), overridable via ORCHESTRA_DIR / test paths.
_STATE_DIR = os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))

from scripts.lineage_daemon.collect import collect_fleet
from scripts.lineage_daemon.decide import decide
from scripts.lineage_daemon.canary import should_act
from scripts.lineage_daemon import executors
from scripts.lineage_daemon.enrich import enrich_statuses
from scripts.lineage_daemon import execute as execute_mod
from scripts.lineage_daemon.execute import (
    execute_rotation, default_approval_gate, default_safety_recheck,
)


def plan(agents: list) -> list:
    """Map each agent observation dict through decide() (pure).

    Returns one decision dict per input agent, in order. Takes no action.
    """
    return [decide(agent) for agent in agents]


def gather_status() -> list:
    """Run `agent-status.py --all` and return the parsed JSON array.

    This is the ONLY live read in the daemon, and it is READ-ONLY: it
    merely scrapes current fleet state. No fleet action is taken.
    """
    out = subprocess.check_output(
        ["python3", "scripts/agent-status.py", "--all"],
        cwd=_ROOT,
        text=True,
    )
    return json.loads(out)


def load_registry(path=None) -> dict:
    """Read registry.json and return the parsed dict.

    Defaults to <worktree-root>/registry.json. On any error, returns
    {"agents": {}} so a missing/corrupt registry can't break a dry-run.
    """
    if path is None:
        path = os.path.join(_STATE_DIR, "registry.json")
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {"agents": {}}


def load_meta(path=None) -> dict:
    """Read state/agent-sessions.json (session_id / resume_command / cwd / model
    per agent), used to locate each session's live .jsonl for the ctx-token
    fallback. On any error, returns {} (enrichment degrades gracefully)."""
    if path is None:
        path = os.path.join(_STATE_DIR, "state", "agent-sessions.json")
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {}


def gather_fleet() -> list:
    """One read-only pass: agent-status --all, then daemon-local ENRICHMENT
    (pane status-bar line + jsonl context tokens + resolved model) for the
    sessions the shared detector left ctx-blind. All reads are read-only."""
    return enrich_statuses(gather_status(), load_meta(), load_registry())


def run_dry_cycle(status_list: list, registry: dict) -> dict:
    """Summarize what a real cycle WOULD do -- pure, takes no action.

    Collects the fleet, plans decisions, and returns counts + the
    actionable (non-noop) subset. It never kills, spawns, or writes.
    """
    decisions = plan(collect_fleet(status_list, registry))

    counts = {"noop": 0, "soft_handoff": 0, "hard_rotate": 0}
    actionable = []
    for d in decisions:
        counts[d["action"]] = counts.get(d["action"], 0) + 1
        if d["action"] != "noop":
            actionable.append({
                "agent_id": d["agent_id"],
                "action": d["action"],
                "reason": d["reason"],
                "needs_approval": d["needs_approval"],
                "tier": d["tier"],
            })

    return {
        "cycle": "dry-run",
        "total": len(decisions),
        "counts": counts,
        "actionable": actionable,
    }


def validate_arm_args(arm, canary):
    """Validate the arm/canary combination (pure).

    Returns an error string on invalid input, else None. In this phase
    arming is refused without an explicit canary -- there is NO blanket
    arm. A canary WITHOUT arm is allowed: it just scopes an observe-only
    preview.
    """
    if arm and not canary:
        return ("arming requires --canary <agent_id> in this phase "
                "(no blanket arm)")
    return None


def _lineage_of(canary, pred_entry):
    """(lineage_root, predecessor_generation) for the canary (Gap 3/7).

    lineage_root = the CANONICAL name: registry lineage_root, else the canary
    stripped of a trailing -gN, else the canary. predecessor_generation =
    registry `generation`, else a trailing -gN in the name, else 1.
    """
    root = pred_entry.get("lineage_root") or re.sub(r"-g\d+$", "", canary) or canary
    gen = pred_entry.get("generation")
    if gen is None:
        m = re.search(r"-g(\d+)$", canary)
        gen = int(m.group(1)) if m else 1
    return root, int(gen)


def armed_plan(agents, canary, arm, force_action, registry=None) -> dict:
    """Compute what WOULD happen per agent, canary-scoped. NEVER executes.

    For each decision from plan(agents):
      * if the agent is not the canary -> mode "observe" (never armed).
      * else -> action = force_action or the decided action; mode is
        "ARMED" when arm else "preview". The planned executor commands are
        built by calling the plan_* funcs with armed=False (cmds only).

    hard_rotate builds the Stage-3 ordered plan (all 7 gaps):
      [verify_no_live_successor, register_successor(Gap1), spawn,
       verify_successor, inject_init(Gap2), wire_edge(Gap3 gen=pred+1),
       verify_edge(Gap5), retire(canary), repin_canonical(Gap7)]
    successor_id = f"{lineage_root}-g{successor_gen}" where
    successor_gen = predecessor_gen + 1 (Gap 3). The edge is wired before
    retire (spec s4); repin-to-canonical runs AFTER retire so the canonical
    name is free (Gap 7). soft_handoff is a handoff-write note only.

    All plan_* are called armed=False, so this function takes NO action.
    """
    agents_reg = (registry or {}).get("agents", {})
    entries = []
    for d in plan(agents):
        aid = d["agent_id"]
        if not should_act(aid, canary):
            entries.append({
                "agent_id": aid,
                "mode": "observe",
                "action": d["action"],
                "reason": d["reason"],
            })
            continue

        action = force_action or d["action"]
        mode = "ARMED" if arm else "preview"

        if action == "hard_rotate":
            pred_entry = agents_reg.get(canary, {})
            lineage_root, pred_gen = _lineage_of(canary, pred_entry)
            generation = pred_gen + 1                     # Gap 3: pred_gen + 1
            successor_id = f"{lineage_root}-g{generation}"   # transient -gN
            plan_steps = [
                {"step": "verify_no_live_successor",
                 "successor": successor_id, "note": "gate before spawn"},
                executors.plan_register_successor(         # Gap 1
                    successor_id, pred_entry, generation, lineage_root,
                    armed=False),
                executors.plan_spawn(successor_id, armed=False),
                executors.verify_successor(successor_id, armed=False),
                executors.plan_inject_init(successor_id, canary, armed=False),  # Gap 2
                executors.plan_wire_edge(canary, successor_id, generation,
                                         armed=False),      # Gap 3 (gen)
                executors.plan_verify_edge(canary, successor_id, armed=False),  # Gap 5
                executors.plan_retire(canary, armed=False),
                executors.plan_repin_canonical(            # Gap 7
                    successor_id, lineage_root, generation, armed=False),
            ]
        else:  # soft_handoff (and any non-rotate forced action)
            plan_steps = [{
                "action": "soft_handoff",
                "note": "signal agent to write fixed-schema handoff + commit",
            }]

        entries.append({
            "agent_id": aid,
            "mode": mode,
            "action": action,
            "reason": d["reason"],
            "plan": plan_steps,
        })

    return {"canary": canary, "armed": arm, "entries": entries}


_SCAFFOLD = {
    "status": "observe-only-scaffold",
    "note": "fleet collection wired in a later gated pass; nothing armed",
}


def main(argv=None) -> int:
    """Observe-only entry.

    --dry-run (default): performs the ONE read-only live read, then prints
    a summary of what a real cycle would do -- it arms nothing and takes no
    fleet action.
    --self-test: prints the observe-only scaffold marker WITHOUT any live
    read, so import/CI stays hermetic.
    """
    parser = argparse.ArgumentParser(
        description="Lineage daemon (observe-only; arms nothing)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Read the fleet read-only and summarize (default).",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        default=False,
        help="Print the scaffold marker with NO live read (hermetic).",
    )
    parser.add_argument(
        "--arm",
        action="store_true",
        default=False,
        help="Arm the canary agent (requires --canary; no blanket arm).",
    )
    parser.add_argument(
        "--canary",
        default=None,
        help="Scope actionability to this ONE agent id (airtight guard).",
    )
    parser.add_argument(
        "--force-action",
        choices=["hard_rotate", "soft_handoff"],
        default=None,
        help="Canary-only affordance to force the action (test path).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="LIVE-EXECUTE the armed hard_rotate for the ONE --canary agent "
             "(requires --arm --canary). Reversible steps run; the KILL is "
             "gated on an execute-time safety re-check AND the operator's one-tap "
             "approval. Manually invoked only -- NEVER cron/@reboot/fleet-wide.",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        print(json.dumps(_SCAFFOLD))
        return 0

    # --execute: the ONLY path that runs armed=True executors. Triple-gated at the
    # entry: requires --arm AND --canary (one agent), and even then the KILL waits
    # on a live safety re-check + the operator's tap inside execute_rotation.
    if args.execute:
        err = validate_arm_args(args.arm, args.canary)
        if err:
            print(json.dumps({"error": err}))
            return 2
        if not args.arm:
            print(json.dumps({"error": "--execute requires --arm --canary"}))
            return 2
        registry = load_registry()
        trace = execute_rotation(
            args.canary, registry,
            approval_fn=default_approval_gate,
            safety_fn=default_safety_recheck,
        )
        print(json.dumps(trace, indent=2))
        return 0 if trace.get("status") in (execute_mod.DONE,) else 1

    # --arm/--canary path (no --execute): print the canary-scoped armed plan as
    # JSON. This still EXECUTES NOTHING -- it is the preview/verify surface.
    if args.arm or args.canary:
        err = validate_arm_args(args.arm, args.canary)
        if err:
            print(json.dumps({"error": err}))
            return 2
        registry = load_registry()
        result = armed_plan(
            collect_fleet(gather_fleet(), registry),
            canary=args.canary,
            arm=args.arm,
            force_action=args.force_action,
            registry=registry,
        )
        # NOTE: without --execute, even --arm executes nothing (preview only).
        print(json.dumps(result, indent=2))
        return 0

    summary = run_dry_cycle(gather_fleet(), load_registry())
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
