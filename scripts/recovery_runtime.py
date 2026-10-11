"""Strict runtime gate for automatic recovery and reincarnation.

This is deliberately narrower than a provider resume adapter.  Automatic
recovery is safety-qualified for Claude and Gemini/agy unconditionally.  Codex
is admitted ONLY for a specific rotation that presents a fully-satisfied C-R7
7-condition gate (codex-dev-1 §4): real SID+transcript, complete/fresh instance
records, live lease+epoch+CAS, exactly-one canonical successor, verified/
recoverable projections, successor first-effect verified BEFORE predecessor
retire, and checks enforced (not shadow).  Absent that gate the refusal stands —
"fence satisfiable alone is insufficient."
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runtime_signatures import RuntimeResolutionError, resolve_runtime


class AutomaticRecoveryRefused(RuntimeError):
    """The row is not safe for an automatic lifecycle verb."""


# The seven conditions (codex §4) that ALL must be True to admit a codex rotation.
CODEX_LIFT_CONDITIONS = (
    "codex_sid_resolved_and_transcript_confirmed",
    "instance_records_complete_and_fresh",
    "recovery_owns_live_lease_epoch_cas",
    "durable_txn_exactly_one_successor",
    "projections_verified_or_recoverable",
    "successor_first_effect_verified",
    "checks_enforced_not_shadow",
)


def _codex_gate_satisfied(codex_gate) -> bool:
    """True iff every one of the seven conditions is EXPLICITLY True. A missing
    key, a falsey value, or a non-dict all fail closed — the caller must PROVE
    every condition, never merely omit one."""
    if not isinstance(codex_gate, dict):
        return False
    return all(codex_gate.get(cond) is True for cond in CODEX_LIFT_CONDITIONS)


def automatic_resume_runtime(entry: dict, *, agent_id: str | None = None,
                             codex_gate: dict | None = None) -> str:
    """Return a supported automatic-resume runtime or raise.

    ``resolve_runtime`` owns normalization and the positive-model fallback.
    Claude/Gemini are admitted unconditionally (``codex_gate`` never affects
    them — byte-unchanged).  Codex is admitted ONLY when ``codex_gate`` proves
    all seven C-R7 conditions; otherwise the refusal stands (fail-closed).
    """
    runtime = resolve_runtime(entry, agent_id=agent_id, strict=True)
    if runtime in ("claude", "gemini"):
        return runtime
    if runtime == "codex":
        if _codex_gate_satisfied(codex_gate):
            return runtime
        raise AutomaticRecoveryRefused(
            "runtime='codex' requires a fully-satisfied C-R7 7-condition gate "
            "(SID+transcript, fresh instance records, live lease+epoch+CAS, one "
            "canonical successor, verified projections, successor first-effect, "
            "enforced-not-shadow) — fence-satisfiable alone is insufficient"
        )
    raise AutomaticRecoveryRefused(
        f"runtime={runtime!r} is not a resumable automatic-recovery seat"
    )


def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", required=True)
    ap.add_argument("--agent", required=True)
    args = ap.parse_args()
    try:
        registry = json.loads(Path(args.registry).read_text())
        entry = (registry.get("agents") or {}).get(args.agent) or {}
        if not entry:
            raise AutomaticRecoveryRefused("registry row is missing")
        print(automatic_resume_runtime(entry, agent_id=args.agent))
        return 0
    except (OSError, json.JSONDecodeError, RuntimeResolutionError,
            AutomaticRecoveryRefused) as exc:
        print(f"REFUSE: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(_main())
