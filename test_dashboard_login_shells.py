"""The web terminal attaches to a login shell only while the API lists it (dashboard-login-shells.js).

"Connect" a provider starts a plain bash tmux session with no registry row; the proxy refused it as unregistered, so
"Open a terminal here" showed "[Connection error] [Disconnected]". The API now lists each shell it starts in
<data dir>/state/login-shells.json. These run the REAL proxy under node and open real WebSocket handshakes: a
`login-*` name alone is refused, a listed one is let through, an expired or removed one is refused again, and the
authenticated-session check in front of it is unchanged. Session names here never exist in tmux, so an accepted
handshake attaches to nothing. A missing node FAILS the test rather than skipping it.
"""
import base64
import hashlib
import hmac
import json
import os
import shutil
import socket
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
SECRET = "sample-session-secret"  # pragma: allowlist secret
LISTED = "login-zz-sample-listed"          # never a real tmux session
UNLISTED = "login-zz-sample-unlisted"


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    return port


def _cookie():
    body = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time() * 1000) + 3600_000}).encode()).rstrip(b"=")
    sig = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).rstrip(b"=")
    return "orchestra_session=" + (body + b"." + sig).decode()


def _write_list(state, shells):
    tmp = state / "login-shells.json.tmp"
    tmp.write_text(json.dumps({"shells": shells}))
    os.replace(tmp, state / "login-shells.json")     # the API renames into place too


def _entry(session, minutes):
    now = datetime.now(timezone.utc)
    return {"session": session, "provider": "codex", "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=minutes)).isoformat(), "created_by": "sample-user"}


@pytest.fixture
def proxy(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.fail("node is required to run dashboard-proxy.js; it is not on PATH")
    state = tmp_path / "state"
    state.mkdir()
    (state / "session-secret").write_text(SECRET)
    (tmp_path / "registry.json").write_text(json.dumps({"agents": {"dev-demo": {"tmux_session": "zz-sample-seat"}}}))
    port = _free_port()
    env = {**os.environ, "ORCHESTRA_API_PORT": str(_free_port()), "ORCHESTRA_DASHBOARD_PORT": str(port),
           "ORCHESTRA_DASHBOARD_HOST": "127.0.0.1", "ORCHESTRA_DIR": str(tmp_path), "SESSION_SECRET": SECRET,
           "ORCHESTRA_TAILSCALE_USERS": ""}
    proc = subprocess.Popen([node, str(ROOT / "dashboard-proxy.js")], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("the proxy did not start: " + (proc.stdout.read() if proc.stdout else ""))
    yield {"port": port, "state": state}
    proc.kill(); proc.wait(timeout=5)


def _handshake(port, session, cookie=True):
    """The status code the proxy answers a /ws/terminal upgrade with."""
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    head = (f"GET /ws/terminal?session={session} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n")
    if cookie:
        head += f"Cookie: {_cookie()}\r\n"
    s.sendall((head + "\r\n").encode())
    first = s.recv(4096).split(b"\r\n", 1)[0].decode()
    s.close()
    return int(first.split()[1])


def test_the_proxy_loads_the_shared_reader():
    assert 'require("./dashboard-login-shells")' in (ROOT / "dashboard-proxy.js").read_text()


def test_an_unlisted_login_shell_is_refused(proxy):
    assert _handshake(proxy["port"], UNLISTED) == 401
    _write_list(proxy["state"], [_entry(LISTED, 30)])
    assert _handshake(proxy["port"], UNLISTED) == 401, "another shell being listed is not enough"


def test_a_listed_login_shell_is_let_through(proxy):
    _write_list(proxy["state"], [_entry(LISTED, 30)])
    assert _handshake(proxy["port"], LISTED) == 101


def test_an_expired_login_shell_is_refused(proxy):
    _write_list(proxy["state"], [_entry(LISTED, -1)])
    assert _handshake(proxy["port"], LISTED) == 401


def test_a_removed_login_shell_is_refused_on_the_next_upgrade(proxy):
    _write_list(proxy["state"], [_entry(LISTED, 30)])
    assert _handshake(proxy["port"], LISTED) == 101
    _write_list(proxy["state"], [])                  # its shell exited and the API swept it
    assert _handshake(proxy["port"], LISTED) == 401


def test_a_registered_seat_still_attaches_with_no_list(proxy):
    assert _handshake(proxy["port"], "zz-sample-seat") == 101


def test_a_listed_shell_still_needs_an_authenticated_session(proxy):
    _write_list(proxy["state"], [_entry(LISTED, 30)])
    assert _handshake(proxy["port"], LISTED, cookie=False) == 401
