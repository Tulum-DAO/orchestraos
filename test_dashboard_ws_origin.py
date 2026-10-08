"""The dashboard WebSocket origin check (dashboard-ws-origin.js).

The web terminal is a shell into a seat's pane, and browsers do not apply the same-origin
policy to WebSockets. These cases pin the two rules: same origin, AND a host the dashboard
serves (so DNS rebinding cannot make Origin and Host agree on an attacker's name).
Runs the real module under node; a missing node FAILS the test rather than skipping it.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
PORT = 8891

CASES = [
    # (name, inputs, expected allow)
    ("no origin (curl, a local script)", {"hostHeader": "127.0.0.1:8891"}, True),
    ("dashboard on loopback", {"origin": "http://127.0.0.1:8891", "hostHeader": "127.0.0.1:8891"}, True),
    ("dashboard as localhost", {"origin": "http://localhost:8891", "hostHeader": "localhost:8891"}, True),
    ("ssh -L tunnel", {"origin": "http://127.0.0.1:8891", "hostHeader": "127.0.0.1:8891", "peerAddress": "127.0.0.1"}, True),
    ("tailscale serve on 443", {"origin": "https://box.tail1234.ts.net", "hostHeader": "box.tail1234.ts.net", "peerAddress": "127.0.0.1"}, True),
    ("tailscale serve on a free port", {"origin": "https://box.tail1234.ts.net:8445", "hostHeader": "box.tail1234.ts.net:8445", "peerAddress": "127.0.0.1"}, True),
    ("listed in ORCHESTRA_DASHBOARD_ALLOWED_HOSTS", {"origin": "https://dash.example.org", "hostHeader": "dash.example.org", "extraHosts": "dash.example.org"}, True),
    # attacks
    ("foreign page vs loopback", {"origin": "https://evil.example", "hostHeader": "127.0.0.1:8891"}, False),
    ("foreign page vs tailnet name", {"origin": "https://evil.example", "hostHeader": "box.tail1234.ts.net", "peerAddress": "127.0.0.1"}, False),
    ("DNS rebinding: attacker name resolving to 127.0.0.1", {"origin": "http://rebind.evil.example:8891", "hostHeader": "rebind.evil.example:8891"}, False),
    ("suffix trick", {"origin": "http://evil-127.0.0.1:8891", "hostHeader": "127.0.0.1:8891"}, False),
    ("loopback origin on another port", {"origin": "http://127.0.0.1:5173", "hostHeader": "127.0.0.1:8891"}, False),
    ("forged X-Forwarded-Host from a non-loopback peer", {"origin": "https://evil.example", "hostHeader": "127.0.0.1:8891", "forwardedHost": "evil.example", "peerAddress": "100.64.0.9"}, False),
    ("malformed origin", {"origin": "not a url", "hostHeader": "127.0.0.1:8891"}, False),
    ("null origin (sandboxed iframe, file://)", {"origin": "null", "hostHeader": "127.0.0.1:8891"}, False),
]


def _node():
    node = shutil.which("node")
    if not node:
        pytest.fail("node is required to run dashboard-ws-origin.js; it is not on PATH")
    return node


def _decide(inputs):
    script = (
        "const { wsOriginDecision } = require(process.argv[1]);"
        "process.stdout.write(JSON.stringify(wsOriginDecision(JSON.parse(process.argv[2]))));"
    )
    args = {"port": PORT, "dashboardHost": "127.0.0.1", "extraHosts": "", **inputs}
    out = subprocess.run(
        [_node(), "-e", script, str(ROOT / "dashboard-ws-origin.js"), json.dumps(args)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return json.loads(out.stdout)


@pytest.mark.parametrize("name,inputs,expected", CASES, ids=[c[0] for c in CASES])
def test_ws_origin_decision(name, inputs, expected):
    d = _decide(inputs)
    assert d["allow"] is expected, f"{name}: {d}"


def test_proxy_wires_the_check_into_both_upgrade_paths():
    src = (ROOT / "dashboard-proxy.js").read_text()
    assert 'require("./dashboard-ws-origin")' in src
    # the terminal WebSocketServer verifies every client
    assert 'path: "/ws/terminal", verifyClient: (info) => wsAllowed(info.req)' in src
    # the /api/* upgrade forward refuses before dialling the API
    api = src.index('if (!req.url.startsWith("/api/")) return;')
    assert "if (!wsAllowed(req))" in src[api:api + 300]
