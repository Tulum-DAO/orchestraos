"""Self-trigger rotation seam — the PRIMARY self-accomplished rotation trigger's
shared contract (gm commission msg_93fd71f5, the operator-directed).

Two triggers cooperate:
  - PRIMARY (self): a live PostToolUse hook (rotation-self-trigger.js) reads the
    agent's OWN ctx% from /tmp/claude-ctx-<session>.json and, crossing the rotation
    threshold, injects a ONE-TIME self-rotate instruction + writes a MARK here.
  - SAFETY-NET (beat, ob's lane): reads the mark to tell 'self-triggered (skip)' from
    'crossed threshold but silent = wedged (nudge)'.

This module is the SEAM both sides share (same discipline as event_schema / the S3
observed-dict): the JS hook writes marks in build_mark's shape; ob's beat calls
classify(). Pure/no-IO. Thresholds are config-driven (module constants).

Convention: `remaining` = REMAINING context percentage (lower = more used). Rotation
fires near ~10% remaining (~90% used) — the operator's handoff-at-90 rule
(memory feedback_handoff_at_90pct).
"""

# Escalating rotation thresholds by REMAINING % (higher remaining = earlier/softer).
AUTHOR_REMAINING = 12      # start authoring your successor handoff now
ROTATION_REMAINING = 10    # rotate now (the primary fire point, ~90% used)
IMMEDIATE_REMAINING = 8    # rotate immediately before auto-compact

# Ladder, most→least severe (used for once-per-level + no-regress firing).
_LEVELS = ("immediate", "rotate", "author")

# classify() verdicts (the handoff-of-responsibility signal). classify is
# DISPOSITION-NEUTRAL: it returns a verdict, it NEVER kills. The beat maps the
# verdict to a tiered disposition (the operator ruling 2026-08-15, gm msg_933ec411):
#   SELF_TRIGGERED -> beat SKIPS (agent is self-rotating).
#   WEDGED         -> beat SOFT-nudges a RECOVERABLE agent to author its handoff
#                     (reversible, arming now); an UNRECOVERABLE/hard case emits an
#                     APPROVAL CARD to the operator (kind='rotation_kill': agent/ctx%/why/
#                     evidence + Approve-kill+respawn/Deny/Defer) and executes the
#                     kill ONLY on the operator's per-kill tap. The beat NEVER auto-kills.
#                     (Tier-3 approval-emit is a SEPARATE build after tiers 1+2 land.)
#   OK             -> below threshold, leave alone.
SELF_TRIGGERED = "self_triggered"
WEDGED = "wedged"
OK = "ok"

# A self-trigger mark is fresh for this long; older = the agent is silent past
# threshold AGAIN (didn't complete the rotation) -> wedged.
# Co-signed with ob (msg_f1dcef22, 2026-08-15): 2700s (45min) > a full rich-rotation
# envelope (author + spawn + orient + cite-back + park-idle 600s cooldown + two-sample
# settle, measured on the live gen-7prime->gen-8 rotation) so a slow-but-ALIVE self-
# rotation is never reclassified wedged mid-flight; kept < 3600 so a truly wedged agent
# is caught within ~45min. An actively-rotating agent re-marks (author->rotate->
# immediate) keeping triggered_at fresh anyway; this is the backstop if it stops.
# ob owns CLEARING the mark at park-idle-retire (rotation-complete); this is belt-and-
# suspenders for a wedged-never-cleared mark. ob passes it explicitly from the beat too.
DEFAULT_MARK_MAX_AGE_S = 2700


def rotation_level(remaining):
    """Escalating level for a REMAINING %: None above AUTHOR, else author/rotate/
    immediate as remaining drops. Lower remaining = higher severity."""
    if remaining is None:
        return None
    if remaining <= IMMEDIATE_REMAINING:
        return "immediate"
    if remaining <= ROTATION_REMAINING:
        return "rotate"
    if remaining <= AUTHOR_REMAINING:
        return "author"
    return None


def _severity(level):
    """0 = none; higher = more severe."""
    if level in _LEVELS:
        return len(_LEVELS) - _LEVELS.index(level)   # immediate=3, rotate=2, author=1
    return 0


def should_fire(level, last_fired):
    """Fire once per LEVEL; a HIGHER-severity level re-fires (escalation), a same/
    lower one does not. No level -> never fire."""
    if not level:
        return False
    return _severity(level) > _severity(last_fired)


def build_mark(session_id, pane, cwd, remaining, level, ts):
    """The durable seam signal the hook writes when it self-triggers. Source identity
    (session_id/pane/cwd) mirrors the event bus's raw-event shape so ob resolves the
    canonical agent at beat time (never string-match)."""
    return {
        "kind": "rotation_self_trigger",
        "session_id": session_id,
        "pane": pane,
        "cwd": cwd,
        "remaining": remaining,
        "level": level,
        "triggered_at": ts,
    }


def classify(crossed_threshold, mark, now, max_age_s=DEFAULT_MARK_MAX_AGE_S):
    """The handoff-of-responsibility predicate ob's safety-net beat calls.

    - not crossed the rotation threshold -> OK (leave alone).
    - crossed + a FRESH mark (agent self-triggered) -> SELF_TRIGGERED (beat SKIPS).
    - crossed + no mark / stale / malformed mark -> WEDGED (beat NUDGES).

    Fail-toward-nudge: an unreadable mark never lets a possibly-wedged agent slip
    silently (safety-net's job is to catch the ones the primary missed)."""
    if not crossed_threshold:
        return OK
    triggered_at = (mark or {}).get("triggered_at") if isinstance(mark, dict) else None
    if triggered_at is None:
        return WEDGED
    if (now - triggered_at) > max_age_s:
        return WEDGED
    return SELF_TRIGGERED
