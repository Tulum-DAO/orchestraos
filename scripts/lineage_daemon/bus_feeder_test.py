"""Tests for bus_feeder.py — the fail-open WS-A bus feeder hook.

Asserts the HARD CONTRACT by effect: maps hook events to raw stream events,
appends fail-closed, and — critically — NEVER raises / always no-ops on any error
(bad stdin, ENOSPC-style append failure, missing pane) so a turn is never wedged.
"""
import json
import os

from scripts.lineage_daemon import bus_feeder
from scripts.lineage_daemon import bus
from scripts.focus_registry.event_schema import validate_raw_event


def test_feed_maps_stop_to_turn_ended(monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%7")
    raw = bus_feeder._feed(json.dumps({
        "hook_event_name": "Stop", "session_id": "sid-1", "cwd": "/w"}))
    assert raw["type"] == "turn_ended"
    assert raw["pane"] == "%7" and raw["session_id"] == "sid-1"
    assert raw["agent"] is None                       # resolve-free at the hook
    assert validate_raw_event(raw)["valid"] is True


def test_feed_maps_pretooluse_with_tool_payload(monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%1")
    raw = bus_feeder._feed(json.dumps({
        "hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "s"}))
    assert raw["type"] == "tool_use"
    assert raw["payload"] == {"tool": "Bash", "phase": "pre"}


def test_feed_permission_notification(monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%1")
    raw = bus_feeder._feed(json.dumps({
        "hook_event_name": "Notification",
        "message": "Claude needs your permission", "session_id": "s"}))
    assert raw["type"] == "notification"
    assert raw["payload"]["kind"] == "permission"


def test_feed_unmapped_event_returns_empty(monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%1")
    assert bus_feeder._feed(json.dumps({"hook_event_name": "PreCompact"})) == {}


def test_feed_never_carries_prompt_body(monkeypatch):
    """UserPromptSubmit -> prompt_submit, and the prompt text is NOT in the event."""
    monkeypatch.setenv("TMUX_PANE", "%1")
    raw = bus_feeder._feed(json.dumps({
        "hook_event_name": "UserPromptSubmit", "prompt": "secret user text",
        "session_id": "s"}))
    assert raw["type"] == "prompt_submit"
    assert "secret user text" not in json.dumps(raw)


# --- HARD CONTRACT: main() is fail-open (never raises) -------------------------

def test_main_no_pane_is_noop(monkeypatch):
    monkeypatch.delenv("TMUX_PANE", raising=False)
    # no pane -> returns immediately, no exception
    bus_feeder.main()


def test_main_appends_event(monkeypatch, tmp_path):
    monkeypatch.setenv("TMUX_PANE", "%3")
    monkeypatch.setattr(bus_feeder, "STREAM_DIR", str(tmp_path / "es"))
    monkeypatch.setattr("sys.stdin", _FakeStdin(json.dumps({
        "hook_event_name": "Stop", "session_id": "sid", "cwd": "/w"})))
    bus_feeder.main()
    out = bus.read_events(str(tmp_path / "es"))
    assert len(out["events"]) == 1 and out["events"][0]["type"] == "turn_ended"


def test_main_swallows_bad_stdin(monkeypatch, tmp_path):
    monkeypatch.setenv("TMUX_PANE", "%3")
    monkeypatch.setattr(bus_feeder, "STREAM_DIR", str(tmp_path / "es"))
    monkeypatch.setattr("sys.stdin", _FakeStdin("this is not json{{{"))
    bus_feeder.main()                                  # must NOT raise
    # nothing appended (bad payload dropped fail-open)
    assert bus.read_events(str(tmp_path / "es"))["events"] == []


def test_main_swallows_append_failure(monkeypatch):
    """An append that RAISES mid-write (ENOSPC/permission analogue) must be swallowed
    — a bus error degrades to delayed-not-lost, NEVER a wedged turn."""
    monkeypatch.setenv("TMUX_PANE", "%3")
    monkeypatch.setattr("sys.stdin", _FakeStdin(json.dumps({
        "hook_event_name": "Stop", "session_id": "s", "cwd": "/w"})))

    def boom(*a, **k):
        raise OSError("ENOSPC: no space left on device")
    monkeypatch.setattr(bus.__class__ if False else bus, "append_event", boom)
    # must NOT raise despite append_event blowing up
    bus_feeder.main()


class _FakeStdin:
    def __init__(self, text):
        self._t = text
    def read(self):
        return self._t
