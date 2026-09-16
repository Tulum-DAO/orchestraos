"""SHADOW gate harness — run telemetryd from THIS branch against the REAL live
fleet WITHOUT disturbing the paused production daemon or the live surfaces.

Safety design (every one of gm's constraints):
  * status.json -> a SCRATCH dir (snapshot_base override); the live
    ~/.orchestra/realtime/status.json is NEVER written.
  * wal_dir -> a SCRATCH dir, so the TELEMETRY_DISABLED / BG_DISABLED e-brake
    check reads the (absent) SCRATCH kill-files — the LIVE state/wal/*_DISABLED
    are NEITHER read NOR removed; the shadow simply runs because its own scratch
    dir has no kill-file. The durable mux writes scratch WAL dbs (throwaway) and
    acquires SCRATCH SeatLocks (separate inodes from the live daemon's locks, so
    zero contention with production).
  * sweep = NO-OP. AttachSweep.sweep() is FLEET-WIDE (re-points every session's
    pipe-pane); the shadow must NEVER call it. Rings are read-only tails of the
    existing live sink files (no truncation, independent offset) — harmless.
  * time-boxed: --seconds bounds the run; tick() is called directly (no
    LivenessLock, so it never blocks on the live daemon's held lock).
  * INERT: reads /proc + source transcripts read-only; writes only under --scratch.

Measures children-inclusive CPU (os.times, incl cutime/cstime) over the window
and prints a status breakdown from the scratch status.json (expect a known-idle,
pm-molevera-shaped seat = idle; no seat streaming/computing off a redraw).

Run (from the branch worktree):
    cd scripts && python3 -m lineage_daemon.shadow_gate --seconds 60
"""
import argparse
import json
import os
import sys
import tempfile
import time


class _NoopSweep:
    """NEVER attaches pipe-panes fleet-wide; the shadow must not touch live wiring."""
    def sweep(self):
        return {"attached": [], "errors": {}, "pruned": []}


def build_shadow(orchestra_dir, scratch):
    from pathlib import Path
    from .seat_builder import _live_resolvers, build_seats, build_turn_resolver
    from .telemetryd import TelemetryDaemon, FAST_POLL_S, SLOW_POLL_S
    from .wal.multiplexer import MultiplexedTailer
    from .realtime.proc_sampler import ProcSampler
    from .realtime.lineage_flag import LineageFlagStore

    orchestra_dir = Path(orchestra_dir)
    scratch_wal = os.path.join(scratch, "wal")
    os.makedirs(scratch_wal, exist_ok=True)
    r = _live_resolvers(orchestra_dir)
    _sessions, agents = r.load_stores(orchestra_dir)
    live = r.live_sessions()
    seats, mux_seats = build_seats(
        agents, live, pane_pid=r.pane_pid, source_path=r.source_path,
        hooks_for=r.hooks_for, ring_for=r.ring_for,
        resolve_generation=r.resolve_generation)
    if os.environ.get("SHADOW_NO_MUX") == "1":
        mux = _NoopSweep()                  # isolate the REALTIME lane (no durable tail)
        mux.tick = lambda: 0
        mux.degraded_lineages = {}
    else:
        mux = MultiplexedTailer(wal_dir=scratch_wal, lock_dir=scratch_wal)  # SCRATCH
        for ms in mux_seats:
            try:
                mux.register(ms)
            except Exception:
                pass
    d = TelemetryDaemon(
        seats=seats, mux=mux, sweep=_NoopSweep(), sampler=ProcSampler(),
        flag_store=LineageFlagStore(os.path.join(scratch_wal, "lineage_flags.json")),
        snapshot_base=scratch,                 # status.json -> SCRATCH, not live
        wal_dir=scratch_wal,                   # e-brake check -> SCRATCH (no live read)
        lock_path=os.path.join(scratch_wal, "telemetryd.lock"),
        # exercise the per-slow-tick refresh so the live-scale CPU measurement
        # includes the batched resolve (read-only: ONE tmux list-panes/tick + direct
        # hook-file reads; no writes, so this stays a safe observer harness).
        live_resolver=r.live_resolver,
        # E2: transcript turn-completion (bounded tail-read of the claude .jsonl the
        # mux already tails) — only for working-hook seats; read-only.
        turn_resolver=build_turn_resolver())
    return d, seats, FAST_POLL_S, SLOW_POLL_S


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--warmup", type=float, default=8.0,
                    help="seconds of untimed warm-up (absorbs the cold full-history "
                         "mux re-tail the live daemon never pays)")
    ap.add_argument("--orchestra-dir",
                    default=os.path.expanduser("~/scripts/agent-orchestra"))
    ap.add_argument("--scratch", default=None)
    ap.add_argument("--idle-seat", default="pm-molevera",
                    help="a known-idle seat to confirm reads idle in the shadow")
    args = ap.parse_args(argv)
    scratch = args.scratch or tempfile.mkdtemp(prefix="telemetry-shadow-")
    d, seats, fast_s, slow_s = build_shadow(args.orchestra_dir, scratch)
    print(f"shadow: {len(seats)} live seats | scratch={scratch} | "
          f"FAST={fast_s}s SLOW={slow_s}s | warmup={args.warmup}s window={args.seconds}s")

    # WARM-UP (excluded from the CPU number): the shadow starts with EMPTY scratch
    # WAL cursors, so its first mux tick re-tails each seat's FULL transcript
    # history (a one-time cost the LIVE daemon never pays — it has persistent
    # cursors) + the first cold git pass. Absorb that before timing so the number
    # is honest STEADY-STATE. Note: the git enricher is 60s-cadence; run a window
    # >=60s (or add the separately-measured ~0.38% git) to include a git pass.
    wu = time.time()
    while time.time() - wu < args.warmup:
        d.tick(now=time.time())
        time.sleep(fast_s)

    t0 = os.times()
    w0 = time.time()
    ticks = 0
    while time.time() - w0 < args.seconds:
        d.tick(now=time.time())
        ticks += 1
        time.sleep(fast_s)
    t1 = os.times()
    dt = time.time() - w0
    cpu = (t1[0] - t0[0]) + (t1[1] - t0[1]) + (t1[2] - t0[2]) + (t1[3] - t0[3])

    # status breakdown from the scratch status.json
    counts, idle_seat_status = {}, None
    try:
        with open(os.path.join(scratch, "status.json")) as fh:
            snap = json.load(fh)
        for sid, seat in (snap.get("seats") or {}).items():
            st = seat.get("status")
            counts[st] = counts.get(st, 0) + 1
            if sid == args.idle_seat:
                idle_seat_status = st
    except OSError:
        pass

    print(json.dumps({
        "schema": "telemetry-shadow-gate/v1",
        "seats": len(seats), "ticks": ticks, "window_s": round(dt, 2),
        "cpu_core_pct_own_plus_children": round(100 * cpu / dt, 4),
        "under_1pct": (100 * cpu / dt) < 1.0,
        "status_counts": counts,
        "idle_seat": args.idle_seat, "idle_seat_status": idle_seat_status,
        "scratch_status_json": os.path.join(scratch, "status.json"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
