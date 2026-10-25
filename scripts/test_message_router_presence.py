"""Human presence is per attached CLIENT input, not session activity.

Session activity also moves on the agent's own output, so a persistent viewer attached to a
busy pane used to read as a human mid-conversation and froze that agent's mail."""
import importlib.util
import inspect
import os
import time

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("message_router_presence",
                                               os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)


class R:
    def __init__(self, out, rc=0):
        self.stdout, self.returncode = out, rc


def test_client_presence_takes_newest_input_per_session(monkeypatch):
    now = time.time()
    out = (f"alice|{int(now - 279)}\n"            # a viewer, idle 279 s
           f"bob|{int(now - 30)}\n"
           f"bob|{int(now - 900)}\n"              # two clients on bob: newest wins
           f"garbage-line\n")
    monkeypatch.setattr(mr, "tmux", lambda *a, **k: R(out))
    ages = mr.client_presence()
    assert 278 <= ages["alice"] <= 281 and 29 <= ages["bob"] <= 32
    assert set(ages) == {"alice", "bob"}


def test_client_presence_tmux_failure_means_no_presence(monkeypatch):
    monkeypatch.setattr(mr, "tmux", lambda *a, **k: R("", rc=1))
    assert mr.client_presence() == {}


def test_idle_viewer_no_longer_freezes_mail():
    # one attached client whose last input was 279 s ago -> deliver (the old rule held to 600 s)
    assert mr.attached_presence_hold({"alice": 279}, "alice", time.time()) is None


def test_active_typing_still_holds_with_release_time():
    now = 1_000_000.0
    d = mr.attached_presence_hold({"bob": 30}, "bob", now)
    assert d and "typed 30s ago" in d and "releases ~" in d
    assert time.strftime("%H:%M:%SZ", time.gmtime(now + mr.CLIENT_ACTIVE_TTL_S - 30)) in d


def test_no_client_no_hold_and_boundary():
    assert mr.attached_presence_hold({}, "bob", time.time()) is None
    assert mr.attached_presence_hold({"bob": mr.CLIENT_ACTIVE_TTL_S}, "bob", time.time()) is None
    assert mr.attached_presence_hold({"bob": mr.CLIENT_ACTIVE_TTL_S - 1}, "bob", time.time())


def test_hold_line_carries_the_presence_detail(monkeypatch):
    lines = []
    monkeypatch.setattr(mr, "log", lines.append)
    hs = {}
    msg = {"id": "m1", "created_at": "2026-10-01T00:00:00+00:00"}
    mr.note_hold(hs, "bob", msg, "attached-active", time.time(), queue_n=1,
                 notify_fn=lambda *a, **k: None, dead_letter_fn=lambda *a, **k: None,
                 park_fn=lambda *a, **k: None, detail="attached client typed 5s ago")
    assert any("attached client typed 5s ago" in ln for ln in lines)


def test_call_site_uses_client_presence_not_session_activity():
    src = inspect.getsource(mr.main)
    assert "client_age = client_presence()" in src
    assert "attached_presence_hold(client_age, session, now)" in src
    assert "if hold_for_attached(attached, idle_secs)" not in src
