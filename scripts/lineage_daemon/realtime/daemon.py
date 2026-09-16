"""B1 real-time overlay daemon — the INERT composition of the real-time lane.

One tick fuses the three seams into a per-seat status + court-scrub-gated token
disposition:
  1. attach-SWEEP (pipe_pane.AttachSweep): the invariant-keeper that re-attaches
     pipe-pane across rotations/crashes/manual spawns.
  2. ONE /proc scan (proc_sampler.ProcSampler), fanned to every seat's pid tree
     (never per-agent — the A0 CPU-HARD rule).
  3. per-seat derive_status (bytes-axis primary; bytes fused from the seat's
     pipe-pane ring) + court-scrub-gated token disposition (emission-time PIN:
     flagged/fail-closed => full block, no live model-voice tokens).

E-BRAKE (HARD): the attach-sweep touches LIVE panes (rotation-adjacent), so a
tick honors the BG_DISABLED kill-switch — while armed the daemon does NOTHING
(no attach, no /proc scan). Same discipline as Build A.

INERT: importing/constructing writes nothing and attaches nothing; only tick()/
run() act, and run() is NOT wired to any cron/systemd unit by this build (the
systemd User=shaw unit is the LAST telemetry-v2 step). run() refuses a sub-floor
poll interval and is bounded by max_iters for tests.
"""
import time
from dataclasses import dataclass

from ..wal import bg_state
from .lineage_flag import LineageFlagStore
from .pipe_pane import AttachSweep, SINK_DIR
from .proc_sampler import ProcSampler
from .provider_profiles import profile_for
from .status_deriver import derive_status
from .token_extractor import disposition_for_stream

MIN_POLL_S = 5.0


@dataclass
class Seat:
    session: str
    lineage_root: str
    runtime: str
    root_pid: int
    ring: object = None       # optional pipe-pane RingBuffer (bytes source)
    hooks: dict = None        # optional claude-only semantic layer (None elsewhere)
    degraded: str = None      # closed-code reason if telemetry can't fully serve this
                              # seat (e.g. "no-generation") — surfaced body-free in status.json


class RealtimeOverlayDaemon:
    def __init__(self, tmux, flag_store, *, proc_root="/proc", wal_dir=None,
                 sink_dir=SINK_DIR):
        self.sweep = AttachSweep(tmux, sink_dir=sink_dir)
        self.sampler = ProcSampler(proc_root=proc_root)
        self.flags = flag_store
        self._wal_dir = wal_dir

    def _bg_disabled(self):
        if not self._wal_dir:
            return False
        import os
        return os.path.exists(bg_state._global_disable_path(self._wal_dir))

    def tick(self, seats):
        """One capture pass. Returns {sweep, status, tokens, scan_count} or
        {skipped: 'bg_disabled'} while the e-brake is armed."""
        if self._bg_disabled():
            return {"skipped": "bg_disabled"}
        sweep = self.sweep.sweep()
        proc = self.sampler.sample([s.root_pid for s in seats])
        status, tokens = {}, {}
        for s in seats:
            ps = proc[s.root_pid]
            pty = s.ring.read_all() if s.ring is not None else b""
            sample = {"bytes_flowing": bool(pty), "cpu_core": ps["cpu_core_pct"],
                      "io_delta": ps["io_delta"], "live_pids": ps["live_pids"],
                      "pane_gone": not ps["alive"], "chrome": {}}
            status[s.session] = derive_status(sample, profile=profile_for(s.runtime),
                                              hooks=s.hooks)
            if pty:
                tokens[s.session] = disposition_for_stream(
                    s.runtime, pty, lineage_root=s.lineage_root, flag_store=self.flags)
        return {"sweep": sweep, "status": status, "tokens": tokens,
                "scan_count": self.sampler.scan_count}

    def run(self, seats, *, interval=MIN_POLL_S, should_stop=None, sleep=time.sleep,
            max_iters=None):
        """INERT supervised loop. Refuses a sub-floor interval; bounded by
        max_iters for tests. Not wired to any cron/systemd unit by this build."""
        if interval < MIN_POLL_S:
            raise ValueError(f"poll interval {interval}s below CPU floor {MIN_POLL_S}s")
        should_stop = should_stop or (lambda: False)
        iters = 0
        while not should_stop():
            self.tick(seats)
            iters += 1
            if max_iters is not None and iters >= max_iters:
                return "max-iters"
            sleep(interval)
        return "stopped"
