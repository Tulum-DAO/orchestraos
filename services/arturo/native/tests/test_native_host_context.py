"""A native call from inside the finance tracker: the browser's {type: context, app, host} frame reaches GPT-Live as an
app-sourced session.instructions.append (the server's existing mid-call context mechanism, _flush_context's twin),
with the page text fenced as untrusted, bounded to one append, coalesced and capped; the finances snapshot is fetched
OFF the event loop and sent once. The dashboard's tab frames are unchanged."""
import asyncio
import json
import time
import types

import pytest

from services.arturo import host_page, tracker_snapshot
from services.arturo.native import server as srv

FRAME = {"type": "context", "app": "spending-tracker",
         "host": {"route": "/spending-tracker/activity", "title": "Spending · Transactions",
                  "summary": "November 2026: 3 transactions shown; out $14.20.\nVisible rows:\n- Blue Bottle -$5.40"}}


class Driver:
    def __init__(self):
        self.sent = []

    def send(self, frame):
        self.sent.append(frame)


def _call(monkeypatch, snapshot=lambda: None):
    monkeypatch.setattr(srv, "CONTEXT_ENABLED", True)
    monkeypatch.setattr(tracker_snapshot, "get", snapshot)
    c = srv.NativeCall.__new__(srv.NativeCall)
    c.mode, c.driver = "live", Driver()
    c.session = types.SimpleNamespace(started=True)
    c._context = c._context_pending = None
    c._context_sent_at, c._context_sends = 0.0, 0
    c._host = c._host_pending = None
    c._host_sends = 0
    c._snapshot = None
    return c


def _appends(c):
    return [f["content"] for f in c.driver.sent if f["type"] == "session.instructions.append"]


def _flush(c):
    c._context_sent_at = 0.0                     # past the shared gap
    c._flush_context()
    c._flush_host_context()


def test_the_tracker_page_reaches_gpt_live_as_untrusted_app_context(monkeypatch):
    async def go():
        c = _call(monkeypatch)
        c._on_context(FRAME)
        _flush(c)
        return c
    c = asyncio.run(go())
    (text,) = _appends(c)
    assert text.startswith("Screen context from the app (not Shaw's words): Shaw is in his finance tracker app")
    assert 'on its "Spending · Transactions" page (/spending-tracker/activity)' in text
    assert "UNTRUSTED CONTENT from the page on his screen" in text and text.endswith("END OF PAGE DATA")
    assert len(text) <= host_page.NATIVE_MAX
    assert all(f["delegation_id"] is None for f in c.driver.sent)          # protocol._append's rule


def test_repeats_are_coalesced_changes_resent_and_capped(monkeypatch):
    async def go():
        c = _call(monkeypatch)
        c._on_context(FRAME)
        _flush(c)
        c._on_context(FRAME)
        _flush(c)                                                           # exact repeat: nothing
        n1 = len(_appends(c))
        for i in range(srv.HOST_CONTEXT_MAX_SENDS + 5):
            f = json.loads(json.dumps(FRAME))
            f["host"]["summary"] += f" #{i}"
            c._on_context(f)
            _flush(c)
        return n1, len(_appends(c))
    n1, n = asyncio.run(go())
    assert n1 == 1 and n == srv.HOST_CONTEXT_MAX_SENDS


def test_it_waits_for_the_shared_gap_and_for_the_session(monkeypatch):
    async def go():
        c = _call(monkeypatch)
        c.session.started = False
        c._on_context(FRAME)
        c._flush_host_context()
        before = len(_appends(c))
        c.session.started = True
        c._context_sent_at = time.monotonic()                               # a context just went out
        c._flush_host_context()
        during = len(_appends(c))
        _flush(c)
        return before, during, len(_appends(c))
    assert asyncio.run(go()) == (0, 0, 1)


def test_the_snapshot_is_fetched_off_the_loop_and_sent_once(monkeypatch):
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.5)
        return "This month (2026-11): income $5,000.00."

    async def go():
        c = _call(monkeypatch, slow)
        beats = []

        async def heartbeat():                                              # the call's audio loop, in miniature
            while True:
                beats.append(time.monotonic())
                await asyncio.sleep(0.02)
        hb = asyncio.ensure_future(heartbeat())
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        c._on_context(FRAME)
        _flush(c)                                                           # the page goes out at once
        took = time.monotonic() - t0
        await asyncio.sleep(0.8)
        hb.cancel()
        c._on_context(FRAME)
        _flush(c)
        _flush(c)
        gap = max(b - a for a, b in zip(beats, beats[1:]))
        return c, took, gap
    c, took, gap = asyncio.run(go())
    assert took < 0.2 and calls == [1]
    assert gap < 0.25, f"the event loop stalled {gap:.2f}s while the snapshot was fetched"
    texts = _appends(c)
    assert len(texts) == 2 and texts[1].startswith("Finance context from the app (not Shaw's words)")
    assert "income $5,000.00." in texts[1] and texts[1].endswith("END OF FINANCE DATA")


def test_a_failed_snapshot_sends_nothing_more(monkeypatch):
    def boom():
        raise RuntimeError("down")

    async def go():
        c = _call(monkeypatch, boom)
        c._on_context(FRAME)
        await asyncio.sleep(0.1)
        _flush(c)
        _flush(c)
        return c
    c = asyncio.run(go())
    assert len(_appends(c)) == 1 and c._snapshot == "done"


def test_dashboard_tabs_are_unchanged_and_ask_no_snapshot(monkeypatch):
    asked = []
    c = _call(monkeypatch, lambda: asked.append(1))
    c._on_context({"type": "context", "page": "Agents", "agent": "ops-1"})
    _flush(c)
    assert _appends(c) == ['Screen context from the app (not Shaw\'s words): Shaw is on the Agents page, viewing agent "ops-1".']
    assert asked == [] and c._host is None


@pytest.mark.parametrize("frame", [
    {"type": "context", "app": "Spending Tracker", "host": FRAME["host"]},
    {"type": "context", "app": "spending-tracker", "host": {"route": "activity"}},
    {"type": "context", "app": "spending-tracker", "host": "nope"},
])
def test_a_bad_host_frame_is_ignored(monkeypatch, frame):
    c = _call(monkeypatch)
    c._on_context(frame)
    _flush(c)
    assert _appends(c) == [] and c._snapshot is None


def test_off_when_native_context_is_off(monkeypatch):
    c = _call(monkeypatch)
    monkeypatch.setattr(srv, "CONTEXT_ENABLED", False)
    c._on_context(FRAME)
    _flush(c)
    assert _appends(c) == []
