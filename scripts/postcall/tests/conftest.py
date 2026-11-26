"""Fence for the post-call commitments suite (DESIGN_post-call-commitments.md §7).

The fence is applied per test by the autouse fixture below, scoped to this directory: the suite runs
inside the repo-wide `scripts` CI job, so nothing here may change the environment of other tests.
scripts/postcall reads every path from the environment at call time (never at import), so a
per-test fence covers everything it does. Child processes inherit the fenced environment, and that
is their fence (scratch approvals DB, ntfy token at a missing path so approval_notify refuses to
push, scratch message store, no router kick). In-process network and subprocess calls are refused;
a test that needs a real child allowlists exactly that argv.
"""
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# The live state dir, the operator's overlay checkout (hex-encoded, as scripts/test_no_personal_path.py
# does, so the suite does not ship that path as a literal) and the assistant config dir.
_OVERLAY = os.path.join("~", bytes.fromhex("736372697074732f6167656e742d6f7263686573747261").decode())
_LIVE_ROOTS = [os.path.realpath(os.path.expanduser(p)) for p in ("~/.orchestra", _OVERLAY, "~/.config/jarvis")]


def _under_live(path):
    real = os.path.realpath(path)
    return any(real == r or real.startswith(r + os.sep) for r in _LIVE_ROOTS)


def _fence_env(tmp):
    return {
        "APPROVAL_DB_PATH": os.path.join(tmp, "approvals.db"),
        "NTFY_TOKEN_FILE": os.path.join(tmp, "no-such-dir", "ntfy-token"),
        "MSG_DB_PATH": os.path.join(tmp, "messages.db"),
        "POSTCALL_MODE": "test",
        "MSG_STORE_NO_KICK": "1",                                            # no router pass from a child
        "POSTCALL_CALENDAR_FILE": os.path.join(tmp, "no-calendar", "shaw.ics"),  # never the live feed
        "POSTCALL_CALENDAR_URL_FILE": os.path.join(tmp, "no-secrets", "cal.url"),  # nor the secret URL
        "POSTCALL_STATE_DIR": os.path.join(tmp, "postcall-state"),
        "SHAW_LOCATION_FILE": os.path.join(FIXTURES, "shaw-location.json"),
        "POSTCALL_CLIENTS_DIR": os.path.join(tmp, "no-clients"),                 # never the live client files
        "POSTCALL_REGISTRY_FILE": os.path.join(tmp, "no-registry", "registry.json"),  # nor the live registry
    }


if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))


class _Refused(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _no_outside_effects(monkeypatch, request, tmp_path):
    # Fresh scratch stores per test, so two tests filing the same op_key never see each other's rows.
    for key, value in _fence_env(str(tmp_path)).items():
        monkeypatch.setenv(key, value)
    for key in [k for k in os.environ if k.startswith("TELEGRAM_")]:
        monkeypatch.delenv(key)
    for key in ("APPROVAL_DB_PATH", "NTFY_TOKEN_FILE", "MSG_DB_PATH", "POSTCALL_STATE_DIR", "POSTCALL_CLIENTS_DIR",
                "POSTCALL_REGISTRY_FILE", "POSTCALL_CALENDAR_URL_FILE"):
        assert not _under_live(os.environ[key]), f"postcall fence: {key} points at a live path"
    assert not os.path.exists(os.environ["NTFY_TOKEN_FILE"]), "postcall fence: ntfy token must be absent"
    real_run, real_popen = subprocess.run, subprocess.Popen

    def _gate(argv):
        allowed = getattr(request.node, "postcall_allow_argv", None) or []  # read late: tests set it in-body
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


@pytest.fixture
def live_roots():
    """Real paths no fenced value may point under."""
    return list(_LIVE_ROOTS)
