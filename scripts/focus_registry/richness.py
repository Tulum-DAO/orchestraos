"""Handoff richness + staleness predicates (RED-TEAM H2).

'A garbage handoff passes the gate; richness is unenforceable by schema.' These pure
predicates give Handoff.validate() a MINIMUM-RICHNESS bar so a hollow-but-parseable
handoff fails BEFORE a successor is spawned against it. ob owns handoff_schema.py; it
imports (or inlines) these. The msg_store open-loop reconciliation is a daemon-side
effect check (not pure) and stays on ob's side.
"""
import math
from typing import Optional

MIN_DECISIONS = 2       # decisions WITH rationale, for a normal session (flat default)
MIN_CANARY = 3          # 3-5 predecessor-authored deep questions (Finding 0)
DEFAULT_MAX_AGE_S = 3600  # a handoff authored at soft and used at hard hours later is stale


def min_decisions_for(session_turns: int) -> int:
    """Scale the required decisions-with-rationale by session length (C4, v2 §S1):
    clamp(1, ceil(turns/60), 3). A long session must distil more decisions; a short
    one needs only one. The daemon passes min_decisions_for(<turns>) into check_richness."""
    if session_turns <= 0:
        return 1
    return max(1, min(3, math.ceil(session_turns / 60)))


def check_richness(handoff: dict, min_decisions: int = MIN_DECISIONS,
                   min_canary: int = MIN_CANARY) -> dict:
    """Structural richness bar. Returns {rich, reasons}. Fails a hollow handoff."""
    reasons = []

    if not (handoff.get("phase_state", {}) or {}).get("next_gate"):
        reasons.append("next_gate is empty (no resume anchor)")

    good_decisions = [
        d for d in (handoff.get("decisions", []) or [])
        if d.get("text") and d.get("rationale")
    ]
    if len(good_decisions) < min_decisions:
        reasons.append(f"fewer than {min_decisions} decisions with rationale ({len(good_decisions)})")

    if not (handoff.get("open_loops", []) or []):
        reasons.append("no open_loops (reconcile against msg_store outstanding)")

    if len(handoff.get("canary_questions", []) or []) < min_canary:
        reasons.append(f"fewer than {min_canary} canary questions (Finding 0 deep-context probes)")

    if not (handoff.get("hazards", []) or []):
        reasons.append("no hazards (top live hazards required for the read-back)")

    return {"rich": not reasons, "reasons": reasons}


def check_staleness(handoff_mtime: float, now: float,
                    max_age_s: float = DEFAULT_MAX_AGE_S) -> dict:
    """A handoff authored at soft (~85%) but used at hard hours later is stale.
    age = now - authoring mtime. Returns {stale, age_s}."""
    age = now - handoff_mtime
    return {"stale": age > max_age_s, "age_s": age}
