"""The cutover switch — the single flag the the operator-armed card flips.

INERT by default: no flag file + env unset => ``is_active`` is False, so every
rewired writer falls back to its legacy JSON write and nothing changes. The
separate the operator-armed switch calls ``arm()`` (create the flag) to make the DB the
write-truth; ``disarm()`` is the rollback (re-point writers to direct JSON, still
current under the strangler).
"""
import os

_FLAG_REL = os.path.join("state", "identity-store-cutover.flag")
_ENV = "IDENTITY_STORE_CUTOVER"


def _orchestra_dir(orchestra_dir=None):
    return orchestra_dir or os.environ.get(
        "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))


def flag_path(orchestra_dir=None):
    return os.path.join(_orchestra_dir(orchestra_dir), _FLAG_REL)


def is_active(orchestra_dir=None):
    """True iff the cutover is armed — env override (for controlled runs) or the
    on-disk flag file (the durable, the operator-armed switch)."""
    if os.environ.get(_ENV) == "1":
        return True
    return os.path.exists(flag_path(orchestra_dir))


def arm(orchestra_dir=None):
    """Flip the switch: the DB becomes the identity write-truth. (the operator-armed.)"""
    p = flag_path(orchestra_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write("armed\n")
    return p


def disarm(orchestra_dir=None):
    """Roll back to direct-JSON writes (the strangler default)."""
    try:
        os.remove(flag_path(orchestra_dir))
    except FileNotFoundError:
        pass


def rollback(orchestra_dir=None):
    """Full cutover rollback — restore the pre-cutover strangler state. Disarm the
    cutover flag (writers go straight back to direct JSON, still fully current),
    disarm the liveness monitor, and lift any freeze. Idempotent; safe to call
    when nothing is armed. There is NO data migration to undo — the legacy JSON
    path was never abandoned during the strangler window. (Stopping the projector
    daemon is the operational step in ROLLBACK-RUNBOOK.md.)"""
    from scripts.identity_store import freeze, monitor  # local: avoid import cycle
    disarm(orchestra_dir)
    monitor.disarm(orchestra_dir)
    freeze.unfreeze(orchestra_dir)
