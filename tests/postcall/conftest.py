"""Fence for the post-call commitments suite (DESIGN_post-call-commitments.md §7).

Set at MODULE level, not in a fixture: pytest imports conftest.py before it collects any test
module, so nothing under scripts/postcall can freeze a live path first. Child processes inherit
this environment, and the environment is their fence (scratch approvals DB, ntfy token at a
missing path so approval_notify refuses to push, scratch message store). In-process network and
subprocess calls are refused by the autouse fixture below; a test that needs a real child
allowlists exactly that argv.
"""
import os
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

_FENCE = tempfile.mkdtemp(prefix="postcall-fence-")
os.environ["APPROVAL_DB_PATH"] = os.path.join(_FENCE, "approvals.db")
os.environ["NTFY_TOKEN_FILE"] = os.path.join(_FENCE, "no-such-dir", "ntfy-token")
os.environ["MSG_DB_PATH"] = os.path.join(_FENCE, "messages.db")
os.environ["POSTCALL_MODE"] = "test"
os.environ["POSTCALL_STATE_DIR"] = os.path.join(_FENCE, "postcall-state")
os.environ["SHAW_LOCATION_FILE"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                                                "shaw-location.json")
for _k in [k for k in os.environ if k.startswith("TELEGRAM_")]:
    del os.environ[_k]

_LIVE_ROOTS = [os.path.realpath(os.path.expanduser(p)) for p in
               ("~/.orchestra", "~/scripts/agent-orchestra", "~/.config/jarvis")]


def _under_live(path):
    real = os.path.realpath(path)
    return any(real == r or real.startswith(r + os.sep) for r in _LIVE_ROOTS)


for _k in ("APPROVAL_DB_PATH", "NTFY_TOKEN_FILE", "MSG_DB_PATH", "POSTCALL_STATE_DIR"):
    assert not _under_live(os.environ[_k]), f"postcall fence: {_k} points at a live path"
assert not os.path.exists(os.environ["NTFY_TOKEN_FILE"]), "postcall fence: ntfy token must be absent"

sys.path.insert(0, os.path.join(REPO, "scripts"))


class _Refused(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _no_outside_effects(monkeypatch, request):
    allowed = getattr(request.node, "postcall_allow_argv", None) or []
    real_run, real_popen = subprocess.run, subprocess.Popen

    def _gate(argv):
        argv = list(argv) if not isinstance(argv, str) else [argv]
        if not any(argv[:len(a)] == a for a in allowed):
            raise _Refused(f"postcall fence: subprocess refused: {argv[:3]}")

    def run(argv, *a, **kw):
        _gate(argv)
        return real_run(argv, *a, **kw)

    def popen(argv, *a, **kw):
        _gate(argv)
        return real_popen(argv, *a, **kw)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", popen)

    import urllib.request

    def _no_net(*a, **kw):
        raise _Refused("postcall fence: network refused")

    monkeypatch.setattr(urllib.request, "urlopen", _no_net)
    yield


@pytest.fixture
def fixture_dir():
    return FIXTURES
