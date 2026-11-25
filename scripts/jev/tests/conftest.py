"""Fences for the Jev suite: fully offline, nothing written outside tmp_path.

  * scripts/ goes on sys.path so `import jev` resolves to THIS tree's package (asserted).
  * Every Jev env var is cleared; JEV_DOTENV points at a missing file so the real
    ~/scripts/agent-orchestra/.env key can never be read; JEV_STATE_DIR / JEV_LOG go to tmp.
  * Outbound sockets are refused except to loopback (the fake provider server).
"""
import os
import socket
import sys

import pytest

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

JEV_ENV = ("JEV_BACKEND", "JEV_LIVE", "TYPESAFE_API_KEY", "OPENROUTER_API_KEY", "JEV_DOTENV", "JEV_LOG",
           "JEV_STATE_DIR", "JEV_GUARD_GATES", "JEV_WRITE_ROOTS", "JEV_GUARD_FAIL", "JEV_COMPACT_LINES",
           "JEV_CONTEXT_WINDOW", "JEV_KEEP_RECENT_TOKENS", "JEV_HOOKS_DISABLED", "JEV_HOOK_ALLOW_MOCK",
           "JEV_HOOK_TIMEOUT_MS")


@pytest.fixture(autouse=True)
def jev_fence(monkeypatch, tmp_path):
    for k in JEV_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("JEV_DOTENV", str(tmp_path / "no-such.env"))
    monkeypatch.setenv("JEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("JEV_LOG", str(tmp_path / "jev.jsonl"))

    real_connect = socket.socket.connect

    def loopback_only(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise OSError(f"jev test fence: outbound connection to {host!r} refused")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    from jev import client
    client.reset_shared()
    yield
    client.reset_shared()
