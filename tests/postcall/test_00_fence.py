"""The fence holds before anything else in this suite runs (design §7)."""
import os
import subprocess

import pytest


def test_env_fence_points_at_scratch():
    for key in ("APPROVAL_DB_PATH", "MSG_DB_PATH", "POSTCALL_STATE_DIR"):
        path = os.path.realpath(os.environ[key])
        assert not path.startswith(os.path.realpath(os.path.expanduser("~/.orchestra")))
        assert not path.startswith(os.path.realpath(os.path.expanduser("~/scripts/agent-orchestra")))
    assert not os.path.exists(os.environ["NTFY_TOKEN_FILE"])
    assert os.environ["POSTCALL_MODE"] == "test"


def test_subprocess_is_refused_without_allowlist():
    with pytest.raises(RuntimeError, match="subprocess refused"):
        subprocess.run(["true"])


def test_network_is_refused():
    import urllib.request
    with pytest.raises(RuntimeError, match="network refused"):
        urllib.request.urlopen("https://ntfy.invalid/")
