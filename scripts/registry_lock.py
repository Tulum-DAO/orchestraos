# scripts/registry_lock.py
"""registry_lock — the shared writer lock for registry.json (Phase B3, spec §6.6.2).

Two agents raced registry.json un-locked on 2026-08-16 (gm gen-9→10 debacle,
critique failure mode #9) and avoided a clobber only by luck. EVERY writer of
registry.json (and the other identity surfaces promoted together with it) must
take this lock first:

    from registry_lock import registry_lock
    with registry_lock():
        ...read-modify-write registry.json...

The lock is an exclusive fcntl.flock on state/registry.lock (created if
missing). flock is per open-file-description, so it serializes across
processes AND across independent acquirers inside one process. Raises
RegistryLockTimeout with a clear message if the lock cannot be acquired
within timeout_s.
"""
from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

ORCHESTRA_DIR = Path(os.environ.get(
    "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
LOCK_FILE = ORCHESTRA_DIR / "state" / "registry.lock"

_POLL_S = 0.05


class RegistryLockTimeout(TimeoutError):
    """Could not acquire the registry writer lock within the timeout."""


@contextmanager
def registry_lock(timeout_s: float = 10, lock_path=None):
    """Acquire the exclusive registry writer flock; release on exit.

    timeout_s : seconds to wait for the lock before raising RegistryLockTimeout.
    lock_path : override the lock file (tests); default state/registry.lock.
    """
    path = Path(lock_path or LOCK_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
    acquired = False
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RegistryLockTimeout(
                        f"registry writer lock {path} not acquired within "
                        f"{timeout_s}s — another promotion/registry write is in "
                        f"flight (or a holder crashed while flocked). Retry, or "
                        f"find the holder: fuser {path}")
                time.sleep(_POLL_S)
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)
