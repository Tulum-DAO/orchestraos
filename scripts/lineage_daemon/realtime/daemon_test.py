"""RED tests for the B1 real-time overlay daemon (INERT composition).

Composes: attach-SWEEP (pipe-pane invariant-keeper) + ONE /proc scan/tick
(fanned, never per-agent) + status derive + court-scrub-gated token disposition.
HARD: honors the BG_DISABLED e-brake (attach-sweep touches live panes =
rotation-adjacent). INERT: nothing runs on import/construct; run() is bounded by
max_iters and refuses a sub-floor poll interval; not wired to cron/systemd.
"""
import os

from lineage_daemon.realtime.daemon import RealtimeOverlayDaemon, Seat, MIN_POLL_S
from lineage_daemon.realtime.lineage_flag import LineageFlagStore
from lineage_daemon.realtime.ring import RingBuffer


class FakeTmux:
    def __init__(self, sessions):
        self.sessions = list(sessions)
        self.attach_calls = []

    def list_sessions(self):
        return list(self.sessions)

    def pipe_pane(self, session, sink):
        self.attach_calls.append(session)


def _mkproc(root, pid, ppid=1, utime=1, stime=1):
    d = os.path.join(root, str(pid)); os.makedirs(d, exist_ok=True)
    fields = [str(pid), "(a)", "S", str(ppid)] + ["0"] * 9 + [str(utime), str(stime)] + ["0"] * 30
    open(os.path.join(d, "stat"), "w").write(" ".join(fields) + "\n")
    open(os.path.join(d, "io"), "w").write("read_bytes: 0\nwrite_bytes: 0\n")


def _clean_flags(tmp):
    p = os.path.join(tmp, "lineage_flags.json")
    import json
    json.dump({"schema": "lineage-flags/v1", "flagged": {}}, open(p, "w"))
    return LineageFlagStore(p)


def test_tick_does_one_proc_scan_for_many_seats(tmp_path):
    proc = str(tmp_path / "proc"); os.makedirs(proc)
    seats = []
    for i, pid in enumerate(range(100, 110)):
        _mkproc(proc, pid)
        seats.append(Seat(session=f"seat{i}", lineage_root=f"r{i}",
                          runtime="claude", root_pid=pid))
    tmux = FakeTmux([(s.session, f"${i}") for i, s in enumerate(seats)])
    d = RealtimeOverlayDaemon(tmux, _clean_flags(str(tmp_path)), proc_root=proc)
    before = d.sampler.scan_count
    d.tick(seats)
    assert d.sampler.scan_count - before == 1     # one scan fanned to 10 seats


def test_tick_runs_attach_sweep(tmp_path):
    proc = str(tmp_path / "proc"); os.makedirs(proc)
    _mkproc(proc, 100)
    tmux = FakeTmux([("gm", "$1")])
    d = RealtimeOverlayDaemon(tmux, _clean_flags(str(tmp_path)), proc_root=proc)
    d.tick([Seat("gm", "r-gm", "claude", 100)])
    assert tmux.attach_calls == ["gm"]


def test_bg_disabled_ebrake_skips_everything(tmp_path):
    proc = str(tmp_path / "proc"); os.makedirs(proc)
    _mkproc(proc, 100)
    wal = str(tmp_path / "wal"); os.makedirs(wal)
    open(os.path.join(wal, "BG_DISABLED"), "w").write("")   # e-brake armed
    tmux = FakeTmux([("gm", "$1")])
    d = RealtimeOverlayDaemon(tmux, _clean_flags(str(tmp_path)),
                              proc_root=proc, wal_dir=wal)
    before = d.sampler.scan_count
    r = d.tick([Seat("gm", "r-gm", "claude", 100)])
    assert r.get("skipped") == "bg_disabled"
    assert tmux.attach_calls == []                 # no attach while e-braked
    assert d.sampler.scan_count == before          # no /proc scan while e-braked


def test_status_streaming_from_ring_bytes(tmp_path):
    proc = str(tmp_path / "proc"); os.makedirs(proc)
    _mkproc(proc, 100)
    ring = RingBuffer(4096); ring.append(b"\x1b[0mtok tok tok")   # live pty bytes
    tmux = FakeTmux([("gm", "$1")])
    d = RealtimeOverlayDaemon(tmux, _clean_flags(str(tmp_path)), proc_root=proc)
    r = d.tick([Seat("gm", "r-gm", "claude", 100, ring=ring)])
    assert r["status"]["gm"] == "streaming"        # bytes-axis primary


def test_status_offline_when_root_pid_dead(tmp_path):
    proc = str(tmp_path / "proc"); os.makedirs(proc)   # pid 999 absent
    tmux = FakeTmux([("gm", "$1")])
    d = RealtimeOverlayDaemon(tmux, _clean_flags(str(tmp_path)), proc_root=proc)
    r = d.tick([Seat("gm", "r-gm", "claude", 999)])
    assert r["status"]["gm"] == "offline_crashed"


def test_token_disposition_blocks_flagged_lineage(tmp_path):
    import json
    proc = str(tmp_path / "proc"); os.makedirs(proc); _mkproc(proc, 100)
    p = os.path.join(str(tmp_path), "lineage_flags.json")
    json.dump({"schema": "lineage-flags/v1", "flagged": {"r-dirty": {"reason": "court"}}},
              open(p, "w"))
    store = LineageFlagStore(p)
    ring = RingBuffer(4096); ring.append(b"contaminated model voice")
    tmux = FakeTmux([("dirty", "$1")])
    d = RealtimeOverlayDaemon(tmux, store, proc_root=proc)
    r = d.tick([Seat("dirty", "r-dirty", "claude", 100, ring=ring)])
    tok = r["tokens"]["dirty"]
    assert tok["stream_mode"] == "block" and tok["streamed"] is False
    assert tok["text"] == ""


def test_token_disposition_streams_clean_lineage(tmp_path):
    proc = str(tmp_path / "proc"); os.makedirs(proc); _mkproc(proc, 100)
    ring = RingBuffer(4096); ring.append(b"\x1b[1mhello\x1b[0m world")
    tmux = FakeTmux([("clean", "$1")])
    d = RealtimeOverlayDaemon(tmux, _clean_flags(str(tmp_path)), proc_root=proc)
    r = d.tick([Seat("clean", "r-clean", "claude", 100, ring=ring)])
    tok = r["tokens"]["clean"]
    assert tok["stream_mode"] == "clean" and tok["text"] == "hello world"


# --- INERT ------------------------------------------------------------------

def test_run_refuses_subfloor_interval(tmp_path):
    import pytest
    d = RealtimeOverlayDaemon(FakeTmux([]), _clean_flags(str(tmp_path)))
    with pytest.raises(ValueError):
        d.run([], interval=1.0)                     # below the 5s CPU floor


def test_run_is_bounded_by_max_iters(tmp_path):
    proc = str(tmp_path / "proc"); os.makedirs(proc); _mkproc(proc, 100)
    d = RealtimeOverlayDaemon(FakeTmux([("gm", "$1")]),
                              _clean_flags(str(tmp_path)), proc_root=proc)
    reason = d.run([Seat("gm", "r-gm", "claude", 100)],
                   interval=MIN_POLL_S, max_iters=3, sleep=lambda s: None)
    assert reason == "max-iters"
