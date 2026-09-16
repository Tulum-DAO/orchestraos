"""Hermeticity belt: NO test may open a socket to the LIVE arturo-proxy (:5071).

By effect (gm msg_bfb82953, 2026-09-16 14:40-14:48 ET): a repo-root pytest run
posted a burst of synthetic /finalize-call ids (conv_idem1, conv_regex_check,
conv_test_abc, conv_xyz789 ...) into the LIVE proxy log and tripped
ios-watch-dev's :5071 respawn guard. The emitter was scripts/test_watch_gateway.py's
voice-call-ended cases, whose handler fires the loopback finalize hop
(_notify_arturo_finalize) and swallows the failure -- so nothing ever went red.

This module patches socket.socket.connect / connect_ex for the duration of a test:
a connect to port 5071 on a loopback host is RECORDED and REFUSED (ConnectionRefusedError,
so best-effort callers keep behaving as if the proxy were down), and the fixture FAILS
the test at teardown naming the offender. Tests exercise :5071 paths through a
monkeypatched transport, never the live port. Same class as the prod-registry and
addressability-log guards in scripts/conftest.py.
"""
import socket

import pytest

LIVE_PORT = 5071
_LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0", ""}


def _is_live_5071(address):
    try:
        host, port = address[0], address[1]
    except (TypeError, IndexError):
        return False
    return int(port) == LIVE_PORT and str(host) in _LOOPBACK


def install(attempts):
    """Patch connect/connect_ex on socket.socket; return an undo callable."""
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex

    def connect(self, address):
        if _is_live_5071(address):
            attempts.append(address)
            raise ConnectionRefusedError(
                f"HERMETIC BELT: test tried to reach the LIVE arturo-proxy at {address}")
        return orig_connect(self, address)

    def connect_ex(self, address):
        if _is_live_5071(address):
            attempts.append(address)
            return 111  # ECONNREFUSED
        return orig_connect_ex(self, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex

    def undo():
        socket.socket.connect = orig_connect
        socket.socket.connect_ex = orig_connect_ex
    return undo


@pytest.fixture(autouse=True)
def no_live_arturo_5071(request):
    """Autouse: refuse + record any socket to loopback :5071, fail the test if any."""
    attempts = []
    undo = install(attempts)
    try:
        yield attempts
    finally:
        undo()
    if attempts:
        pytest.fail(
            f"HERMETICITY LEAK: {request.node.nodeid} opened {len(attempts)} socket(s) to the "
            f"LIVE arturo-proxy :5071 ({attempts[0]}). Stub the transport "
            f"(e.g. monkeypatch watch_gateway._notify_arturo_finalize) or point the URL at a "
            f"fixture server; a live POST from a test tripped the :5071 respawn guard "
            f"(gm msg_bfb82953).")
