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
# The cases are SHARED with the API (api/src/lib/ws-origin.test.ts), so the two checks cannot drift.

CASES = [
    (c["name"], c["in"], c["allow"])
    for c in json.loads((ROOT / "contract" / "ws-origin-cases.json").read_text())["cases"]
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
