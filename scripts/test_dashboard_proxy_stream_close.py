"""The dashboard proxy (dashboard-proxy.js) ends its request to the API when the browser goes away.

A live stream (the agent page's transcript SSE) never finishes on its own, so if the proxy kept its upstream request
open after the browser left, the API would never see the stream close and would keep its per-agent tailer ticking
(tmux lookups every second) for good, even for an agent retired long ago. These run the REAL proxy under node in front
of a stand-in API, and check that the API side sees the close within a few seconds, and that an ordinary request is
still proxied whole. A missing node FAILS the test; missing root npm deps skip it.
"""
import http.server
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent   # the checkout: dashboard-proxy.js is at its root


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    return port


class _Api(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port):
        self.stream_closed = threading.Event()
        super().__init__(("127.0.0.1", port), _Handler)


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.endswith("/transcript/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            # Heartbeats until the proxy drops the connection: a failed write is the close the API would see.
            try:
                while True:
                    self.wfile.write(b": ping\n\n"); self.wfile.flush()
                    time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.server.stream_closed.set()
            return
        if self.path.endswith("/slow"):
            time.sleep(1.5)                                    # the browser gives up before this answers
        body = b'{"ok": true, "sample": "whole body"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def proxy(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.fail("node is required to run dashboard-proxy.js; it is not on PATH")
    # The proxy needs the checkout's root npm deps (ws, node-pty). A job that never ran `npm ci` at the root has
    # none, and that says nothing about the proxy: skip there, run wherever the proxy itself could run.
    if subprocess.run([node, "-e", "require.resolve('ws')"], cwd=ROOT, capture_output=True).returncode != 0:
        pytest.skip("the root npm deps (ws) are not installed; run `npm ci` at the checkout root")
    api_port, port = _free_port(), _free_port()
    api = _Api(api_port)
    threading.Thread(target=api.serve_forever, daemon=True).start()
    env = {**os.environ, "ORCHESTRA_API_PORT": str(api_port), "ORCHESTRA_DASHBOARD_PORT": str(port),
           "ORCHESTRA_DASHBOARD_HOST": "127.0.0.1", "ORCHESTRA_DIR": str(tmp_path)}
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
    yield {"port": port, "api": api}
    proc.kill(); proc.wait(timeout=5)
    api.shutdown()


def _get(port, path):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAccept: text/event-stream\r\n\r\n".encode())
    return s


def test_a_stream_the_browser_leaves_is_closed_upstream(proxy):
    s = _get(proxy["port"], "/api/agents/sample/transcript/stream")
    first = s.recv(4096)
    assert b"200" in first.split(b"\r\n", 1)[0]
    s.close()                                                # the browser goes away
    assert proxy["api"].stream_closed.wait(5), "the proxy kept its request to the API open"


def test_an_ordinary_request_is_still_proxied_whole(proxy):
    s = _get(proxy["port"], "/api/health")
    data = b""
    while b"whole body" not in data:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    s.close()
    head, _, body = data.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200") and body == b'{"ok": true, "sample": "whole body"}'
    assert not proxy["api"].stream_closed.is_set()


def test_a_browser_that_leaves_before_the_api_answers_does_not_take_the_proxy_down(proxy):
    s = _get(proxy["port"], "/api/slow")
    time.sleep(0.2)
    s.close()                                                # gone before any response
    time.sleep(2)
    s = _get(proxy["port"], "/api/health")                   # the proxy still serves the next request
    data = b""
    while b"whole body" not in data:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    s.close()
    assert data.startswith(b"HTTP/1.1 200") and b"whole body" in data
