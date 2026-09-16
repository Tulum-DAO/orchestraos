"""Tests for bus.py — the WS-A hook-event bus transport (build+test only).

Hermetic: stream + cursor live under tmp_path; resolve_fn/send_fn are FAKES. No
real hook, tmux, DB, or cron. Asserts the five locked pieces by effect: fail-closed
append, agent-resolution-at-INGEST, nonce-keyed idempotent promotion, monotonic
receipt stamping, event_id-dedup drain, and the liveness watchdog decision.
"""
import json

from scripts.lineage_daemon import bus
from scripts.focus_registry.event_schema import build_raw_event, RECEIPT_LADDER


def _raw(t="turn_ended", pane="%3", session_id="sid-1", cwd="/w", nonce=None):
    return build_raw_event(t, pane=pane, session_id=session_id, cwd=cwd, nonce=nonce)


RESOLVE = lambda pane, sid, cwd: "agent:gm"        # canonical resolver (fake)
RESOLVE_NONE = lambda pane, sid, cwd: None          # rotation gap / dead session


class FakeStore:
    def __init__(self):
        self.rows = {}                              # msg_id -> row (PK dedup)

    def send(self, row):
        self.rows.setdefault(row["msg_id"], row)    # idempotent on msg_id
        return row["msg_id"]


# --- 1. transport WRITER (append, fail-closed) --------------------------------

def test_append_writes_one_json_line(tmp_path):
    sd = str(tmp_path / "event-stream")
    ev = _raw()
    r = bus.append_event(ev, stream_dir=sd, now=1786731957)
    assert r["appended"] is True
    with open(r["path"]) as fh:
        lines = fh.read().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["event_id"] == ev["event_id"]


def test_append_is_fail_closed_on_malformed_raw(tmp_path):
    sd = str(tmp_path / "event-stream")
    bad = _raw()
    bad["pane"] = None
    bad["session_id"] = None          # no source identity -> raw-invalid
    r = bus.append_event(bad, stream_dir=sd, now=0)
    assert r["appended"] is False
    assert any("source identity" in e for e in r["errors"])


def test_append_then_read_roundtrips_and_skips_garbage(tmp_path):
    sd = str(tmp_path / "event-stream")
    bus.append_event(_raw(), stream_dir=sd, now=1786731957)
    bus.append_event(_raw(t="session_end"), stream_dir=sd, now=1786731957)
    # inject a garbage line
    with open(bus.stream_path(sd, now=1786731957), "a") as fh:
        fh.write("not json\n")
    out = bus.read_events(sd, now=1786731957)
    assert len(out["events"]) == 2 and out["skipped_lines"] == 1


# --- 3. INGEST: agent resolved at ingest (Q2) ---------------------------------

def test_ingest_resolves_and_stamps_agent():
    ev = _raw()
    assert ev["agent"] is None                       # raw carries no agent
    r = bus.ingest(ev, resolve_fn=RESOLVE)
    assert r["ok"] is True
    assert r["event"]["agent"] == "agent:gm"         # stamped at ingest


def test_ingest_unresolved_agent_is_not_ingestable():
    r = bus.ingest(_raw(), resolve_fn=RESOLVE_NONE)
    assert r["ok"] is False
    assert any("unresolved" in e for e in r["errors"])


# --- 4. advance_receipt wiring (monotonic) ------------------------------------

def test_stamp_advances_receipt_monotonic():
    ev = _raw()
    assert ev["receipt"]["status"] == "emitted"
    d = bus.stamp(ev, "delivered")
    assert d["receipt"]["status"] == "delivered" and d["receipt"]["delivered_at"]
    # regressing is a no-op
    back = bus.stamp(d, "emitted")
    assert back["receipt"]["status"] == "delivered"


# --- 3. PROMOTION: nonce-keyed idempotent delivery row ------------------------

def test_promote_builds_delivered_row_keyed_by_nonce():
    ev = bus.ingest(_raw(nonce="abc123def456"), resolve_fn=RESOLVE)["event"]
    store = FakeStore()
    p = bus.promote(ev, send_fn=store.send)
    assert p["promoted"] and p["row_id"] == "evt-abc123def456"
    row = store.rows["evt-abc123def456"]
    assert row["to_agent"] == "agent:gm" and row["type"] == "turn_ended"
    assert row["receipt"]["status"] == "delivered"


def test_promote_same_nonce_does_not_double_row():
    ev = bus.ingest(_raw(nonce="dup00dup00dup"), resolve_fn=RESOLVE)["event"]
    store = FakeStore()
    bus.promote(ev, send_fn=store.send)
    bus.promote(ev, send_fn=store.send)               # re-delivered
    assert len(store.rows) == 1                        # PK 'evt-<nonce>' dedupes


# --- 2. turn-boundary CONSUMER beat -------------------------------------------

# The interim default is OBSERVE-ONLY (ACTIONABLE_TYPES = ()). The promotion
# MECHANISM is still tested by passing an explicit `actionable` set — this is what
# the future real-delivery step will drive; the default promoting nothing is the
# gm-directed interim that stopped the phantom-row flood.
_TEST_ACTIONABLE = ("turn_ended", "session_end", "notification")


def test_default_is_observe_only_no_promotion():
    """INTERIM (gm 2026-08-15): the DEFAULT promotes NOTHING — stops the phantom
    self-addressed [bus] flood. Events are still consumed/seen (liveness record)."""
    store = FakeStore()
    evs = [_raw(t="turn_ended"), _raw(t="session_end"), _raw(t="notification")]
    out = bus.consume_beat(evs, resolve_fn=RESOLVE, send_fn=store.send)
    assert len(out["processed"]) == 3                  # all consumed (observed)
    assert out["promoted"] == []                        # NOTHING promoted (default)
    assert store.rows == {}


def test_consume_beat_promotes_actionable_and_dedups():
    store = FakeStore()
    evs = [_raw(t="turn_ended"), _raw(t="prompt_submit"), _raw(t="session_end")]
    out = bus.consume_beat(evs, resolve_fn=RESOLVE, send_fn=store.send,
                           actionable=_TEST_ACTIONABLE)
    # all three processed; only the two actionable types promoted to rows
    assert len(out["processed"]) == 3
    promoted_types = {store.rows[p["row_id"]]["type"] for p in out["promoted"]}
    assert promoted_types == {"turn_ended", "session_end"}    # prompt_submit not actionable


def test_consume_beat_is_idempotent_on_rerun():
    store = FakeStore()
    evs = [_raw(t="turn_ended"), _raw(t="session_end")]
    first = bus.consume_beat(evs, resolve_fn=RESOLVE, send_fn=store.send,
                             actionable=_TEST_ACTIONABLE)
    # re-run over the SAME window with the carried `seen` -> nothing re-processed
    again = bus.consume_beat(evs, resolve_fn=RESOLVE, send_fn=store.send,
                             seen=first["seen"], actionable=_TEST_ACTIONABLE)
    assert again["processed"] == [] and again["promoted"] == []
    assert all(s["reason"] == "duplicate-or-idless" for s in again["skipped"])
    assert len(store.rows) == 2                        # no duplicate rows


def test_consume_beat_skips_unresolved_without_promoting():
    store = FakeStore()
    out = bus.consume_beat([_raw()], resolve_fn=RESOLVE_NONE, send_fn=store.send)
    assert out["processed"] == [] and out["promoted"] == []
    assert store.rows == {}


# --- 5. cron liveness WATCHDOG -------------------------------------------------

def test_beat_is_stale():
    assert bus.beat_is_stale(None, 1000) is True           # never beat
    assert bus.beat_is_stale(1000, 1000 + 60) is False     # within window
    assert bus.beat_is_stale(1000, 1000 + 600) is True     # gap too large


def test_watchdog_decides_restart_when_stale():
    live = bus.watchdog_check({"last_beat_ts": 1000}, now=1030)
    assert live["live"] is True and live["action"] == "ok"
    dead = bus.watchdog_check({"last_beat_ts": 1000}, now=1000 + 10_000)
    assert dead["live"] is False and dead["action"] == "restart-and-repage"


# --- cursor persistence -------------------------------------------------------

def test_cursor_roundtrip_and_seen_cap(tmp_path):
    path = str(tmp_path / "bus-cursor.json")
    cur = {"last_beat_ts": 1786731957.0, "seen": [f"e{i}" for i in range(6000)]}
    bus.save_cursor(path, cur)
    got = bus.load_cursor(path)
    assert got["last_beat_ts"] == 1786731957.0
    assert len(got["seen"]) == 5000                       # bounded
    assert got["seen"][-1] == "e5999"


def test_load_cursor_missing_is_fresh(tmp_path):
    got = bus.load_cursor(str(tmp_path / "nope.json"))
    assert got == {"last_beat_ts": None, "seen": []}
