"""pytest configuration for orchestra_cli tests.

Tests should run in isolation from the running seatproof sandbox environment,
which sets ORCHESTRA_CONFIG, ORCHESTRA_DIR, ORCHESTRA_ROOT, and ORCH_DIR.
These are unset for each test here to ensure test fixtures work correctly.

Per test, not once per session: a session-wide unset outlived these tests and removed the root
conftest's data-dir fence for every test that ran after them, so a later module load fell back to
the real data dir and the write guard refused it (the root conftest's per-test environ restore
snapshots after this fixture, so it restored the variables as missing).
"""
import pytest


@pytest.fixture(autouse=True)
def isolate_from_seatproof(monkeypatch):
    """Unset orchestra environment variables that would interfere with tests."""
    for key in ("ORCHESTRA_CONFIG", "ORCHESTRA_DIR", "ORCHESTRA_ROOT", "ORCH_DIR"):
        monkeypatch.delenv(key, raising=False)
