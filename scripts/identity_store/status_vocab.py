"""Canonical status vocabulary — Identity Layer v1 item (b) (gm-specified).

`canonical.status` must be ONE of four meanings; nothing can reason over 15 ad-hoc
strings. The enum + the by-effect legacy resolution live HERE so every writer and the
reconciler share one source of truth.

    online      = a live process on THIS host (item a: tmux_session exists AND its pane
                  pid has >=1 live child).
    parked      = no live process, resumable (resume_command preserved).
    provisional = a green minted, not yet promoted (bg lane; left as-is).
    retired     = the generation is retired — a canonical row should NOT carry this
                  (flagged, never auto-written by the reconciler).

LEGACY_MAP resolves the historical zoo. Liveness-dependent maps are resolved BY EFFECT
at apply time (the SAME liveness probe as item a), NEVER by string alone:

    quiescent, stopped, killed, offline, n/a, retiring, registered -> parked (static)
    active, ready  -> online IF live else parked
    spawning       -> provisional IF the gen is not yet promoted, else online/parked by liveness
"""

STATUS_ENUM = frozenset({"online", "parked", "provisional", "retired"})

# static legacy -> parked (no live process implied by the label)
_TO_PARKED = frozenset({"quiescent", "stopped", "killed", "offline", "n/a",
                        "retiring", "registered"})
# legacy (and the enum online/parked) resolved purely by liveness
_BY_LIVENESS = frozenset({"active", "ready", "online", "parked"})

# Human-readable mapping (for the dry-run table + docs). The real resolution is resolve().
LEGACY_MAP = {
    "quiescent": "parked", "stopped": "parked", "killed": "parked", "offline": "parked",
    "n/a": "parked", "retiring": "parked", "registered": "parked",
    "active": "online-if-live-else-parked", "ready": "online-if-live-else-parked",
    "spawning": "provisional-if-not-promoted-else-liveness",
}


class InvalidStatus(ValueError):
    """A writer attempted to persist a canonical.status outside STATUS_ENUM."""


def assert_valid(status):
    """Enforcement for every canonical.status write. Raises InvalidStatus on a
    non-enum value so the zoo can never re-accrete through a sanctioned writer."""
    if status not in STATUS_ENUM:
        raise InvalidStatus(
            f"canonical.status={status!r} is not in the enum {sorted(STATUS_ENUM)} "
            f"— route it through status_vocab.resolve() first")
    return status


def resolve(status, *, is_live, gen_promoted=True):
    """Resolve ANY current status to an enum value BY EFFECT.

    ``is_live`` (item-a liveness) and ``gen_promoted`` (the canonical gen's promoted_at
    is set) are the effect inputs. ``provisional``/``retired`` are returned unchanged
    (provisional = bg lane; retired = flagged by the reconciler, not remapped here).
    Unknown labels fail SAFE to ``parked`` (never silently ``online``).
    """
    s = (status or "").strip().lower()
    if s in ("provisional", "retired"):
        return s
    if s in _TO_PARKED:
        return "parked"
    if s == "spawning":
        if not gen_promoted:
            return "provisional"
        return "online" if is_live else "parked"
    if s in _BY_LIVENESS:
        return "online" if is_live else "parked"
    return "parked"  # unknown legacy -> fail-safe parked
