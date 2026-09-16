"""Tests for bus_beat.py — the WS-A bus drain entrypoint.

Asserts by effect: a drain promotes actionable events to msg_store rows, is
idempotent across re-drains via the persisted cursor (dead-bus = delayed-not-lost,
drain-from-cursor with no double-promote), advances the cursor, and the default
resolver reverse-maps source identity -> canonical head via succession.
"""
import json

import pytest

from scripts.lineage_daemon import bus_beat
from scripts.lineage_daemon import bus
from scripts.focus_registry.event_schema import build_raw_event


@pytest.fixture(autouse=True)
def _isolate_runtime_drain_sentinel(tmp_path, monkeypatch):
    """Hermetic: point the non-synced runtime e-brake dir at a clean tmp so tests
    never pick up a live ~/runtime/BUS_DRAIN_DISABLED. Tests exercising the sentinel
    pass orchestra_dir=tmp_path (in-tree)."""
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(tmp_path / "runtime"))


def _seed_stream(stream_dir, types, now):
    for t in types:
        ev = build_raw_event(t, pane="%3", session_id="sid-1", cwd="/w")
        bus.append_event(ev, stream_dir=stream_dir, now=now)


RESOLVE = lambda pane, sid, cwd: "agent:gm"


class FakeStore:
    def __init__(self):
        self.rows = {}
    def send(self, row):
        self.rows.setdefault(row["msg_id"], row)
        return row["msg_id"]


# The interim DEFAULT is observe-only (bus.ACTIONABLE_TYPES == ()); the promotion
# MECHANISM is exercised by passing an explicit actionable set (what the future
# real-delivery step drives).
_ACT = ("turn_ended", "session_end", "notification")


def test_drain_default_is_observe_only(tmp_path):
    """INTERIM (gm 2026-08-15): default drain promotes NOTHING (stops phantom flood);
    events still consumed + marked seen (liveness record)."""
    sd = str(tmp_path / "es")
    now = 1786785676
    _seed_stream(sd, ["turn_ended", "session_end"], now)
    store = FakeStore()
    r = bus_beat.run_drain(stream_dir=sd, cursor={"last_beat_ts": None, "seen": []},
                           resolve_fn=RESOLVE, send_fn=store.send, now=now)
    assert r["consumed"]["promoted"] == []                   # nothing promoted
    assert store.rows == {}
    assert len(r["cursor"]["seen"]) == 2                     # still consumed/seen


def test_drain_promotes_actionable(tmp_path):
    sd = str(tmp_path / "es")
    now = 1786785676
    _seed_stream(sd, ["turn_ended", "prompt_submit", "session_end"], now)
    store = FakeStore()
    r = bus_beat.run_drain(stream_dir=sd, cursor={"last_beat_ts": None, "seen": []},
                           resolve_fn=RESOLVE, send_fn=store.send, now=now, actionable=_ACT)
    promoted_types = {store.rows[p["row_id"]]["type"] for p in r["consumed"]["promoted"]}
    assert promoted_types == {"turn_ended", "session_end"}   # prompt_submit not actionable
    assert r["cursor"]["last_beat_ts"] == now
    assert len(r["cursor"]["seen"]) == 3                      # all 3 processed+seen


def test_drain_is_idempotent_across_redrains(tmp_path):
    """Dead-bus / restart: re-draining the SAME window with the carried cursor
    promotes NOTHING new (event_id dedup) — delayed-not-lost, no double-promote."""
    sd = str(tmp_path / "es")
    now = 1786785676
    _seed_stream(sd, ["turn_ended", "session_end"], now)
    store = FakeStore()
    first = bus_beat.run_drain(stream_dir=sd, cursor={"last_beat_ts": None, "seen": []},
                               resolve_fn=RESOLVE, send_fn=store.send, now=now, actionable=_ACT)
    assert len(first["consumed"]["promoted"]) == 2
    # re-drain with the returned cursor -> nothing re-promoted
    second = bus_beat.run_drain(stream_dir=sd, cursor=first["cursor"],
                                resolve_fn=RESOLVE, send_fn=store.send, now=now + 60,
                                actionable=_ACT)
    assert second["consumed"]["promoted"] == []
    assert len(store.rows) == 2                               # no duplicate rows


def test_drain_unresolved_agent_stays_in_stream(tmp_path):
    """An event whose agent can't be resolved is NOT promoted (stays for a later
    drain) — never delivered to a wrong address."""
    sd = str(tmp_path / "es")
    now = 1786785676
    _seed_stream(sd, ["turn_ended"], now)
    store = FakeStore()
    r = bus_beat.run_drain(stream_dir=sd, cursor={"last_beat_ts": None, "seen": []},
                           resolve_fn=lambda p, s, c: None, send_fn=store.send, now=now)
    assert r["consumed"]["promoted"] == []
    assert store.rows == {}
    assert r["cursor"]["seen"] == []                          # not marked seen -> retried later


def test_drain_watchdog_flags_stale_previous_beat(tmp_path):
    sd = str(tmp_path / "es")
    now = 1786785676
    _seed_stream(sd, ["turn_ended"], now)
    store = FakeStore()
    # previous beat was long ago -> watchdog says restart-and-repage
    r = bus_beat.run_drain(stream_dir=sd,
                           cursor={"last_beat_ts": now - 10_000, "seen": []},
                           resolve_fn=RESOLVE, send_fn=store.send, now=now)
    assert r["watchdog"]["action"] == "restart-and-repage"


def test_hot_days_window():
    days = bus_beat.hot_days(now=1786785676, window_days=2)
    assert len(days) == 2 and days[0] < days[1]               # oldest first, 2 days


# --- kill-switch sentinel (the operator arm-condition) --------------------------------

def test_drain_disabled_is_noop(tmp_path, monkeypatch):
    """Authoritative (runtime) sentinel present -> instant pure no-op: no promote,
    cursor UNCHANGED."""
    runtime = tmp_path / "runtime"; runtime.mkdir()
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(runtime))
    (runtime / "BUS_DRAIN_DISABLED").write_text("e-stop")
    sd = str(tmp_path / "es")
    now = 1786785676
    _seed_stream(sd, ["turn_ended", "session_end"], now)
    store = FakeStore()
    cursor = {"last_beat_ts": now - 5, "seen": ["prior-id"]}
    r = bus_beat.run_drain(stream_dir=sd, cursor=cursor, resolve_fn=RESOLVE,
                           send_fn=store.send, now=now, orchestra_dir=str(tmp_path))
    assert r["disabled"] is True
    assert r["consumed"]["promoted"] == []            # nothing promoted
    assert store.rows == {}                            # no rows written
    assert r["cursor"] == cursor                       # cursor UNCHANGED (frozen)
    assert r["watchdog"]["action"] == "disabled"


def test_drain_predicate_runtime_authoritative(tmp_path, monkeypatch):
    """HARDENED (gm msg_b8f1c614): ONLY the non-synced runtime path brakes; the in-tree
    synced path is advisory (drain_disabled_advisory), never a hard brake."""
    runtime = tmp_path / "runtime"; runtime.mkdir()
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(runtime))
    orch = tmp_path / "orch"; (orch / "state").mkdir(parents=True)
    assert bus_beat.drain_disabled(str(orch)) is False
    # in-tree only -> NOT braked, but advisory True
    (orch / "state" / "BUS_DRAIN_DISABLED").write_text("stale")
    assert bus_beat.drain_disabled(str(orch)) is False
    assert bus_beat.drain_disabled_advisory(str(orch)) is True
    # runtime sentinel -> braked (authoritative)
    (runtime / "BUS_DRAIN_DISABLED").write_text("x")
    assert bus_beat.drain_disabled(str(orch)) is True


def test_estop_then_resume_no_dropped_events(tmp_path, monkeypatch):
    """The load-bearing reinstate proof: events that arrive DURING the e-stop are NOT
    lost — after rm-sentinel the next drain resumes from the saved cursor and delivers
    exactly them, once."""
    runtime = tmp_path / "runtime"; runtime.mkdir()
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(runtime))
    sentinel = runtime / "BUS_DRAIN_DISABLED"
    sd = str(tmp_path / "es")
    store = FakeStore()

    # t0: one event, normal drain -> promoted; cursor saved.
    _seed_stream(sd, ["turn_ended"], now=1000)
    r1 = bus_beat.run_drain(stream_dir=sd, cursor={"last_beat_ts": None, "seen": []},
                            resolve_fn=RESOLVE, send_fn=store.send, now=1000,
                            orchestra_dir=str(tmp_path), actionable=_ACT)
    assert len(r1["consumed"]["promoted"]) == 1
    saved_cursor = r1["cursor"]

    # E-STOP engaged; a NEW event arrives during the stop.
    sentinel.write_text("stop")
    _seed_stream(sd, ["session_end"], now=1000)        # arrives while disabled
    r2 = bus_beat.run_drain(stream_dir=sd, cursor=saved_cursor, resolve_fn=RESOLVE,
                            send_fn=store.send, now=1030, orchestra_dir=str(tmp_path),
                            actionable=_ACT)
    assert r2["disabled"] is True
    assert len(store.rows) == 1                         # the during-stop event NOT delivered
    assert r2["cursor"] == saved_cursor                 # cursor frozen

    # REINSTATE: rm sentinel -> next drain resumes from saved cursor, delivers the
    # backlog event exactly once (no drop, no double).
    sentinel.unlink()
    r3 = bus_beat.run_drain(stream_dir=sd, cursor=saved_cursor, resolve_fn=RESOLVE,
                            send_fn=store.send, now=1060, orchestra_dir=str(tmp_path),
                            actionable=_ACT)
    assert r3["disabled"] is False
    assert len(store.rows) == 2                         # the backlog event now delivered
    types = {row["type"] for row in store.rows.values()}
    assert types == {"turn_ended", "session_end"}       # both, exactly once


def test_default_resolve_follows_succession(tmp_path, monkeypatch):
    """The default resolver reverse-maps session_id -> agent and follows
    succeeded_by to the canonical head."""
    meta = {
        "gm": {"session_id": "sid-old", "tmux_session": "gm", "succeeded_by": "gm-gen8"},
        "gm-gen8": {"session_id": "sid-new", "tmux_session": "gm-gen8"},
    }
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "agent-sessions.json").write_text(json.dumps(meta))
    resolve = bus_beat.default_resolve(orchestra_dir=str(tmp_path))
    # an event from the OLD predecessor session resolves to the canonical head
    assert resolve("%1", "sid-old", "/w") == "gm-gen8"
    # a direct event to the head resolves to itself
    assert resolve("%2", "sid-new", "/w") == "gm-gen8"
    # unknown -> None
    assert resolve("%9", "sid-unknown", "/w") is None
