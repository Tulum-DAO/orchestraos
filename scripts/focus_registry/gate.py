"""The rotation gate (RED-TEAM v2 contract) — floor ∧ comprehension ∧ effect.

Finding 0 verdict: 'on-track' is the right FLOOR, the wrong GATE. This composes the
three orthogonal checks into the real confirm-gate the daemon opens before notify +
retire:

  confirmed = floor(check_on_track)          # behavior: misfocus / stall / dead-spawn
            AND comprehension(read-back+canary) # KNOWLEDGE: absorbed the deep context
            AND effect(first-action effect)     # EFFECT: produced a checkable result

All three are pure/mechanical (daemon-gradeable, no LLM). Comprehension is graded vs
DAEMON-HELD ground truth; the successor's evidence is a CLAIM, never trusted by
arrival. A behaviorally-perfect but half-informed gen-7 fails on comprehension.
"""
from typing import Callable, Optional

from scripts.focus_registry.correctness import check_on_track
from scripts.focus_registry.comprehension import check_comprehension
from scripts.focus_registry.effects import check_effect


def rotation_gate(
    observed: dict,
    expected: dict,
    evidence: dict,
    ground_truth: dict,
    effect: Optional[dict] = None,
    cwd: str = ".",
    effect_runner: Optional[Callable] = None,
    completion_mode: bool = False,
) -> dict:
    """Confirm a successor only if it is behaviorally on-track AND demonstrably
    comprehends the deep context AND (if a first-action effect is declared) that
    effect exists. Returns {confirmed, floor, comprehension, effect, reasons}.

    `completion_mode` (DEC-1787808620) SPLITS the gate for the multi-beat completion
    re-confirm of a HELD readback-only successor (a DIFFERENT verification contract
    than the spawn-time confirm). When True:
      * EFFECT is NOT required pre-promote (it is a post-promote artifact per item #4,
        verified within-N-beats-else-ROLLBACK by the completion path). The gate never
        runs check_effect and never flags effect-absent.
      * The FLOOR is satisfied via observed.oriented (a committed readback) INSTEAD of
        live focus_registry membership — a held pre-promote successor has no live focus
        and is parked by design. GENUINE not-oriented AND wrong-file failures are KEPT;
        the focus-membership and progression reasons are exempt.
    Comprehension is UNCHANGED (the near-sole absorption proof — strictness preserved).
    completion_mode=False (default) is byte-identical to the spawn-time behavior."""
    floor = check_on_track(observed, expected)
    comp = check_comprehension(evidence or {}, ground_truth or {})

    if effect and not completion_mode:
        eff = check_effect(effect, cwd=cwd, runner=effect_runner)
        effect_ok = eff.get("exists", False)
    elif completion_mode:
        # effect deferred to the post-promote verify-else-rollback (item #4).
        eff = {"exists": True, "applicable": False, "completion_deferred": True}
        effect_ok = True
    else:
        eff = {"exists": True, "applicable": False}
        effect_ok = True

    floor_reasons = list(floor["reasons"])
    floor_effect_override = False
    if completion_mode:
        # Keep ONLY genuine not-oriented / wrong-file failures; exempt the
        # focus-membership + progression reasons (by-design for a held successor).
        floor_reasons = [r for r in floor_reasons
                         if r.startswith("not oriented")
                         or r.startswith("wrong file area")]
    elif effect and effect_ok and not floor["on_track"]:
        # NEG-2 effect-first override (v2 §S3.3): a produced effect is non-lagging
        # proof of progress, so it EXCUSES the floor's lagging 'not progressing'
        # reason (stale idle/pane telemetry) — but NOT a genuine wrong-focus /
        # not-oriented / wrong-file failure. Only fires when a first_effect exists.
        residual = [r for r in floor_reasons if not r.startswith("not progressing")]
        if len(residual) < len(floor_reasons):        # a not-progressing reason was present
            floor_effect_override = True
            floor_reasons = residual
    floor_ok = not floor_reasons                       # on-track after the override

    reasons = []
    if not floor_ok:
        reasons += [f"floor:{r}" for r in floor_reasons]
    if not comp["comprehended"]:
        reasons.append("comprehension:not-absorbed")
        reasons += [f"canary-missed:{m}" for m in comp["canary"]["missed"]]
        reasons += [f"readback-missed:{m}" for m in comp["readback"]["missed"]]
    if effect and not effect_ok:
        reasons.append(f"effect-absent:{eff.get('kind')}:{eff.get('target')}")

    return {
        "confirmed": floor_ok and comp["comprehended"] and effect_ok,
        "floor": floor,
        "floor_effect_override": floor_effect_override,
        "comprehension": comp,
        "effect": eff,
        "reasons": reasons,
    }
