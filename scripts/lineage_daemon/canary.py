"""Canary scoping guard -- the airtight actionability gate.

The single most important safety feature of the armed phase: while a
canary agent_id is set, ONLY that exact agent is ever actionable. No
canary => nothing actionable. This is a pure predicate; it takes no
action and reads no state.
"""


def should_act(agent_id, canary) -> bool:
    """True only when a canary is set and agent_id matches it exactly.

    Any None (either side) or any mismatch is not actionable.
    """
    return canary is not None and agent_id == canary
