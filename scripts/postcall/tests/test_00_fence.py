"""The fence holds before anything else in this suite runs (design §7)."""
import os
import subprocess

import pytest


def test_env_fence_points_at_scratch(live_roots):
    assert len(live_roots) == 3
    for key in ("APPROVAL_DB_PATH", "MSG_DB_PATH", "POSTCALL_STATE_DIR", "POSTCALL_CLIENTS_DIR",
                "POSTCALL_REGISTRY_FILE"):
        path = os.path.realpath(os.environ[key])
        for root in live_roots:
            assert not path.startswith(root)
    assert not os.path.exists(os.environ["NTFY_TOKEN_FILE"])
    assert os.environ["POSTCALL_MODE"] == "test"


def test_subprocess_is_refused_without_allowlist():
    with pytest.raises(RuntimeError, match="subprocess refused"):
        subprocess.run(["true"])


def test_network_is_refused():
    import urllib.request
    with pytest.raises(RuntimeError, match="network refused"):
        urllib.request.urlopen("https://ntfy.invalid/")
