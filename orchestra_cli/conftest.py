"""pytest configuration for orchestra_cli tests.

Tests should run in isolation from the running seatproof sandbox environment,
which sets ORCHESTRA_CONFIG, ORCHESTRA_DIR, ORCHESTRA_ROOT, and ORCH_DIR.
These are unset for all tests to ensure test fixtures work correctly.
"""
import os
import pytest


@pytest.fixture(scope="session", autouse=True)
def isolate_from_seatproof():
    """Unset orchestra environment variables that would interfere with tests."""
    for key in ("ORCHESTRA_CONFIG", "ORCHESTRA_DIR", "ORCHESTRA_ROOT", "ORCH_DIR"):
        os.environ.pop(key, None)
