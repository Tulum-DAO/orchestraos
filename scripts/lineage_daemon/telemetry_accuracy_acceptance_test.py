"""End-to-end acceptance suite for the telemetry accuracy fix (DEC-1788493111
§9.3) — the binary-per-fixture bundle, driven through the REAL TelemetryDaemon
(FakeRing/FakeSampler stand-ins for the pipe-pane ring + /proc scan).

Binary acceptance (spec §9.3):
  #1  fleet redraw burst           -> seat does NOT leave idle
  #2  idle + resident MCP subtree  -> idle
  #3  stalled CLI + worker child   -> computing (setsid lesson preserved)
  #5  real generation burst        -> streaming in <=1 fast tick
  #9  heavy MCP query while idle   -> idle (uncorroborated resident excluded)
  #10 blocked-on-resident-MCP, multi-minute pty-silent delegated call
      -> computing (resident corroborated by recent novel bytes / working hook)
"""
import os

from .realtime.daemon import Seat
from .realtime.snapshot import read_status_snapshot
from .telemetryd import TelemetryDaemon, FAST_POLL_S, SLOW_POLL_S, CORROBORATION_S
from .telemetryd_test import FakeRing, FakeSampler, FakeSweep, FakeMux, FakeFlags


def _seat(session, root_pid, ring=None, runtime="claude", hooks=None):
    return Seat(session=session, lineage_root="lin-" + session, runtime=runtime,
                root_pid=root_pid, ring=ring, hooks=hooks)


def _proc(own_cpu=0.0, worker_cpu=0.0, resident_cpu=0.0,
          own_io=0, worker_io=0, resident_io=0, live=2, alive=True):
    return {"cpu_core_pct": own_cpu + worker_cpu + resident_cpu,
            "io_delta": own_io + worker_io + resident_io,
            "own_cpu_core_pct": own_cpu, "worker_cpu_core_pct": worker_cpu,
            "resident_cpu_core_pct": resident_cpu, "own_io_delta": own_io,
            "worker_io_delta": worker_io, "resident_io_delta": resident_io,
            "live_pids": live, "alive": alive}


def _mk(tmp, seats, sampler, clock, **kw):
    return TelemetryDaemon(
        seats=seats, mux=FakeMux(), sweep=FakeSweep(), sampler=sampler,
        flag_store=FakeFlags(), snapshot_base=str(tmp), wal_dir=None,
        lock_path=os.path.join(str(tmp), "t.lock"), clock=clock, **kw)


def _status(tmp, session):
    return read_status_snapshot(str(tmp))["seats"][session]["status"]


def _real_screen():
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "..", "..", "contract", "transcript",
                           "fixtures", "claude", "b1-idle-screen.ansi"), "rb") as fh:
        return fh.read()


# --- #2 / #9 idle with a resident MCP subtree -> idle -----------------------

def test_idle_with_resident_mcp_subtree_reads_idle(tmp_path):
    # own CLI idle; a resident MCP child burns 200KB io. No turn-activity.
    sampler = FakeSampler({100: _proc(resident_io=200000, resident_cpu=6.0)})
    d = _mk(tmp_path, [_seat("pm-molevera", 100)], sampler, clock=lambda: 0.0)
    d.tick(now=0.0)
    assert _status(tmp_path, "pm-molevera") == "idle"


# --- #3 stalled CLI + worker child -> computing (setsid lesson) --------------

def test_stalled_cli_with_worker_child_reads_computing(tmp_path):
    sampler = FakeSampler({200: _proc(worker_io=200000)})
    d = _mk(tmp_path, [_seat("builder", 200, runtime="codex")], sampler, clock=lambda: 0.0)
    d.tick(now=0.0)
    assert _status(tmp_path, "builder") == "computing"


# --- #10 blocked-on-resident-MCP, corroborated -> computing -----------------

def test_blocked_on_resident_mcp_corroborated_by_recent_novel_bytes(tmp_path):
    # a delegated MCP call: a novel tool_call burst, then MINUTES of pty silence
    # while the resident MCP works. Recent novel bytes corroborate -> computing.
    now = {"t": 0.0}
    ring = FakeRing()
    sampler = FakeSampler({300: _proc(resident_io=200000, resident_cpu=8.0)})
    d = _mk(tmp_path, [_seat("codex-dev-1", 300, ring=ring, runtime="codex")],
            sampler, clock=lambda: now["t"])
    ring.feed(b"\nDelegating to the search MCP now...\n")   # novel tool_call output
    d.tick(now=0.0)                                          # fast: streaming
    # 30s later: bytes recency (5s) expired, but within the corroboration window
    now["t"] = 30.0
    d.tick(now=30.0)                                         # slow re-classify
    assert _status(tmp_path, "codex-dev-1") == "computing"


def test_blocked_on_resident_mcp_corroborated_by_working_hook(tmp_path):
    # claude leg: the working hook corroborates the delegated call directly.
    sampler = FakeSampler({400: _proc(resident_io=200000, resident_cpu=8.0)})
    seat = _seat("gm", 400, hooks={"state": "working"})
    d = _mk(tmp_path, [seat], sampler, clock=lambda: 0.0)
    d.tick(now=0.0)
    assert _status(tmp_path, "gm") == "computing"


def test_resident_activity_uncorroborated_after_window_returns_idle(tmp_path):
    # once the corroboration window lapses with no further turn-activity, a
    # resident MCP's background churn must fall back to idle (no false-compute).
    now = {"t": 0.0}
    ring = FakeRing()
    sampler = FakeSampler({500: _proc(resident_io=200000, resident_cpu=8.0)})
    d = _mk(tmp_path, [_seat("agy-ops", 500, ring=ring, runtime="gemini")],
            sampler, clock=lambda: now["t"])
    ring.feed(b"\nsome earlier output\n")
    d.tick(now=0.0)
    now["t"] = CORROBORATION_S + 10.0            # well past the window
    d.tick(now=now["t"])
    assert _status(tmp_path, "agy-ops") == "idle"


# --- #1 fleet redraw burst -> stays idle ------------------------------------

def test_fleet_redraw_burst_does_not_leave_idle(tmp_path):
    now = {"t": 0.0}
    ring = FakeRing()
    sampler = FakeSampler({100: _proc()})        # fully idle proc
    d = _mk(tmp_path, [_seat("gm", 100, ring=ring)], sampler, clock=lambda: now["t"])
    screen = _real_screen()
    # 1) the daemon first renders the real screen (seeds the tail; brief streaming)
    ring.feed(screen)
    d.tick(now=0.0)
    # 2) let bytes-recency lapse and re-classify to idle
    now["t"] = 10.0
    d.tick(now=10.0)
    assert _status(tmp_path, "gm") == "idle"
    # 3) a SIGWINCH redraw re-emits the SAME screen (cursor-home + repaint)
    ring.feed(b"\x1b[H\x1b[2J" + screen)
    now["t"] = 11.0
    d.tick(now=11.0)                              # fast tick sees the redraw
    assert _status(tmp_path, "gm") == "idle", "redraw must NOT flip idle->streaming"


# --- #5 real generation -> streaming in one fast tick -----------------------

def test_real_generation_burst_reads_streaming_in_one_tick(tmp_path):
    ring = FakeRing()
    sampler = FakeSampler({100: _proc()})
    d = _mk(tmp_path, [_seat("gm", 100, ring=ring)], sampler, clock=lambda: 0.0)
    d.tick(now=0.0)                              # idle
    ring.feed(b"\nHere is the analysis you asked for: the root cause is ...\n")
    d.tick(now=1.0)                             # one fast tick
    assert _status(tmp_path, "gm") == "streaming"
