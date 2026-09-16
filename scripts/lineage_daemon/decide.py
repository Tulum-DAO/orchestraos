"""Context-exhaustion lineage decision function (pure).

Problem B of docs/superpowers/specs/2026-08-12-orchestra-registry-and-
lineage-daemon.md: the survival/quality trigger. Maps one agent's
observations to a lineage action -- with NO side effects. A death signal
dominates context pressure; the HARD tier forces a rotate; the SOFT tier
asks for a graceful mid-phase handoff; everything else is a noop.

The predecessor-kill for T0/T1 agents is gated behind a one-tap human
approval (needs_approval); the spawn+verify+edge-wire may proceed but the
actual kill must wait. This function only *decides* -- it never acts.
"""

from scripts.lineage_daemon.ctxstate import context_pct, tier
from scripts.lineage_daemon.death import death_signal

# Registry tiers whose predecessor-kill must wait for human approval.
_GATED_TIERS = {"T0", "T1"}


def decide(agent: dict) -> dict:
    """Map one agent's observations to a lineage action (pure).

    Priority: a death signal -> hard_rotate; else ctx HARD -> hard_rotate;
    ctx SOFT -> soft_handoff; otherwise noop. hard_rotate on a T0/T1 agent
    sets needs_approval (the kill is gated).
    """
    sig = death_signal(agent["death"])
    t = tier(context_pct(agent["ctx"]))

    if sig is not None:
        action, reason = "hard_rotate", f"death:{sig}"
    elif t == "HARD":
        action, reason = "hard_rotate", "ctx:HARD"
    elif t == "SOFT":
        action, reason = "soft_handoff", "ctx:SOFT"
    else:
        action, reason = "noop", f"ctx:{t}"

    needs_approval = action == "hard_rotate" and agent["tier_class"] in _GATED_TIERS

    return {
        "agent_id": agent["agent_id"],
        "action": action,
        "reason": reason,
        "needs_approval": needs_approval,
        "tier": t,
        "death": sig,
    }
