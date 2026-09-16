"""Hermeticity guard for identity-store tests (commission mandate — DP-U4).

The unified identity store lives at ``state/orchestra-registry.db``. These tests
must NEVER touch it — every test uses a ``tmp_path`` sandbox DB. This autouse
guard fingerprints the prod store before/after each test and FAILS any test that
mutates it (the rotate_agent prod-leak lesson: a leaked identity row is a phantom
that can page pulse or become a recovery/spawn target).

CUTOVER-FLAG HERMETICITY (learned once cutover went LIVE — gm): the writer modules'
``_cutover_active()`` reads a flag path derived from their ``ORCHESTRA_DIR`` /
``ORCH_DIR`` module global AT CALL TIME (no longer a frozen import-time constant). So
any test that exercises a flag-OFF path MUST monkeypatch that dir global to its
``tmp_path`` sandbox — otherwise, while the live cutover flag is ARMED, the check reads
the REAL armed flag and the test silently flips to the cutover branch (untrustworthy
green that masks regressions). The full suite is run with the live flag reachable as the
trustworthy-green gate.
"""
import hashlib
import os
from pathlib import Path

import pytest

_PROD_DB = Path(
    os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))
) / "state" / "orchestra-registry.db"


_ORCH = os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))
_CUTOVER_FLAG = os.path.join(_ORCH, "state", "identity-store-cutover.flag")
_REGEN_LOCK = os.path.join(_ORCH, "state", "identity-store-regenerator.lock")


def _fingerprint():
    try:
        return hashlib.sha256(_PROD_DB.read_bytes()).hexdigest()
    except OSError:
        return None  # absent -> nothing to protect (fingerprints compare equal)


def _prod_db_is_live_active():
    """True when the prod DB is a legitimately MOVING target — the cutover is armed AND a
    live regenerator daemon holds its lock (so the running fleet is writing the DB +
    reprojecting concurrently). In that state a content-fingerprint guard CANNOT attribute
    a change to the test vs the live fleet, so it must not hard-fail on it."""
    if not os.path.exists(_CUTOVER_FLAG):
        return False
    try:
        pid = int(open(_REGEN_LOCK).read().strip() or "0")
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)  # liveness check
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.fixture(autouse=True)
def _guard_prod_identity_store_unmutated():
    """Fail any test that mutates the production orchestra-registry.db. Tests must point
    every connection at a tmp_path sandbox, never the ORCHESTRA_DIR default.

    LIVE-ACTIVE EXEMPTION: once the cutover is armed and the regenerator daemon is running,
    the prod DB is written continuously by the live fleet — a content fingerprint then
    changes for reasons UNRELATED to the test (false-positive leak). In that state the
    guard cannot distinguish test-writes from fleet-writes, so it skips the assertion
    (tests remain tmp_path-sandboxed; the call-time flag-hermeticity fix keeps flag reads
    off prod). The guard is fully active in its original condition (prod DB quiescent)."""
    if _prod_db_is_live_active():
        yield
        return
    before = _fingerprint()
    yield
    after = _fingerprint()
    assert before == after, (
        f"HERMETICITY LEAK: this test mutated the PRODUCTION identity store at "
        f"{_PROD_DB}. Identity-store tests MUST use a tmp_path sandbox DB.")
