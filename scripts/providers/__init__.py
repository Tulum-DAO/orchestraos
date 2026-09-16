"""Provider resolution — which agent runtime a registry row declares.

The fleet has no provider concept today: `registry.json` carries `model` on 51
rows (all claude-*) and `kind` on 9, but `provider` on none of its 168. Every
write path hardcodes Claude — `spawn-agent.sh` assembles a `claude_cmd`,
`agent-recovery.sh` pins `CLAUDE_BIN`, and `agent-status.py` decides liveness
with `os.path.basename(first_arg) == 'claude'`.

Absent therefore MUST mean claude. Introducing the field any other way would
retroactively reclassify all 168 existing rows.

Everything here fails toward the fleet's existing behaviour except the resume
gate, which fails CLOSED — a provider we cannot classify is never handed to the
claude resume path.
"""

import os

CLAUDE = "claude"

# Expected process basename per provider. This is the predicate `agent-status.py`
# gets wrong today: it asks "is the basename 'claude'" of every pane, so a live
# `agy` (10 days up) and a live `codex` both answer "No claude process running".
_PROCESS_BASENAME = {
    "claude": "claude",
    "codex": "codex",
    "gemini": "agy",
}

# basename -> provider. Inverted from the above so the two can never drift.
_RUNTIME_BY_BASENAME = {v: k for k, v in _PROCESS_BASENAME.items()}


def provider_for(agent_id, registry):
    """The provider an agent declares, defaulting to claude.

    `registry` is the `agents` mapping, keyed by agent id (the `name` field is
    a display name and must never be used for lookup).
    """
    row = registry.get(agent_id) or {}
    return row.get("provider") or CLAUDE


def process_basename(provider):
    """The process basename that proves this provider's agent is alive."""
    return _PROCESS_BASENAME.get(provider)


def runtime_for_command(first_arg):
    """Which agent runtime this argv belongs to, or None.

    Matched on EXACT BASENAME. The detector's own v2 note records that suffix
    matching "false-matched anything ending in 'claude'", and the live argv
    makes the trap concrete: claude and codex appear bare while agy appears as
    an absolute path, so anything looser than basename equality either misses
    agy or over-matches everything.

    This is process truth — the kernel-level floor. It answers "is an agent
    runtime alive on this pane, and which", WITHOUT consulting any store, so
    it holds when the registry is stale, when no provider is declared, and
    when no app-server is attached. Declaration still governs ACTION (see
    claude_resume_is_safe); process governs LIVENESS.
    """
    if not first_arg:
        return None
    return _RUNTIME_BY_BASENAME.get(os.path.basename(first_arg))


def claude_resume_is_safe(provider):
    """Whether `claude --resume <sid>` may be typed at this agent's pane.

    agent-recovery.sh runs every 2 minutes (service-watchdog.sh:286, crontab
    */2) and recovers by `send-keys "$CLAUDE_BIN --resume $sid"`. Its
    eligibility check is fail-OPEN on a missing registry status
    (`if _status and _status not in ('online','active')`), so a non-claude row
    that ever acquires a session_id becomes eligible by default.

    Fail closed: only a provider positively known to be Claude is safe.
    """
    return provider == CLAUDE
