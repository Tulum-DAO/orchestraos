"""Cutover freeze barrier — quiesce writers so ZERO writes are lost at the switch.

The migrate->switch window is the one place a write can be lost: a legacy JSON
write that lands AFTER the final migrate snapshot but BEFORE the flag flips would
be neither in the DB nor survive the projector's first overwrite. The freeze
closes it:

    freeze() -> (settle) migrate() -> cutover.arm() -> unfreeze()

Every writer calls ``barrier()`` before writing. While frozen it BLOCKS, so no
write lands during the migrate; after unfreeze the cutover flag is active and the
same write goes to the DB. The window is bounded by the migrate time (~sub-second
for the whole live fleet) and by ``barrier``'s timeout as a safety valve.
"""
import os
import time

_FREEZE_REL = os.path.join("state", "identity-store-freeze.flag")


class FreezeTimeout(Exception):
    """A writer's barrier waited longer than its timeout for the freeze to lift —
    a safety valve so a stuck cutover cannot wedge the fleet's writers forever."""


def _orchestra_dir(orchestra_dir=None):
    return orchestra_dir or os.environ.get(
        "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))


def freeze_path(orchestra_dir=None):
    return os.path.join(_orchestra_dir(orchestra_dir), _FREEZE_REL)


def is_frozen(orchestra_dir=None):
    return os.path.exists(freeze_path(orchestra_dir))


def freeze(orchestra_dir=None):
    p = freeze_path(orchestra_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write("frozen\n")
    return p


def unfreeze(orchestra_dir=None):
    try:
        os.remove(freeze_path(orchestra_dir))
    except FileNotFoundError:
        pass


def barrier(orchestra_dir=None, timeout=30.0, poll=0.02):
    """Block until the freeze lifts (or return immediately if not frozen). Raise
    FreezeTimeout after ``timeout`` seconds so a stuck cutover cannot wedge
    writers indefinitely."""
    if not is_frozen(orchestra_dir):
        return
    deadline = time.monotonic() + timeout
    while is_frozen(orchestra_dir):
        if time.monotonic() >= deadline:
            raise FreezeTimeout(
                f"writer barrier timed out after {timeout}s waiting for cutover "
                f"freeze to lift")
        time.sleep(poll)
