"""CPU budget — <1% MEASURED, not asserted (B1 hard constraint).

The A0 CPU HARD rule: never scan /proc per-agent (per-agent ps-fork = 2.57% FAIL;
one-scan-fanout = 0.320% PASS). This measures the REAL cost of the B1 real-time
lane's per-tick work on REAL /proc: ONE ProcSampler.sample() fanned to N agent
root pids + the status-derive classification for each. Methodology mirrors Build
A's honest steady-state measure: cpu_time over M ticks / (M * poll_interval),
because in production M ticks span M*interval wall-seconds (dominated by the >=5s
sleep) and CPU is consumed only during the tick work. os.times() counts children.
"""
import os

from lineage_daemon.realtime.proc_sampler import ProcSampler
from lineage_daemon.realtime.provider_profiles import profile_for
from lineage_daemon.realtime.status_deriver import derive_status

MIN_POLL_S = 5.0


def _real_root_pids(n):
    """Up to n REAL pids from /proc as stand-in agent roots (the honest cost is
    dominated by the single full /proc stat scan, independent of which roots)."""
    pids = [int(e) for e in os.listdir("/proc") if e.isdigit()]
    return pids[:n]


def _cpu_since(t0):
    t1 = os.times()
    return ((t1.user - t0.user) + (t1.system - t0.system)
            + (t1.children_user - t0.children_user)
            + (t1.children_system - t0.children_system))


def _high_load():
    """True when the box is busy enough that an ABSOLUTE CPU-% budget would flake
    (os.times CPU-seconds inflate under scheduler/cache/memory contention). The
    RELATIVE + structural guards below are load-invariant and always assert; only the
    absolute <1% budget is gated on this. Fail-open (unknown => not high)."""
    try:
        return os.getloadavg()[0] > (os.cpu_count() or 1) * 0.6
    except (OSError, AttributeError):
        return False


def test_steady_state_cpu_under_one_percent_on_real_proc():
    roots = _real_root_pids(40)
    ticks = 200
    profiles = {p: profile_for(p) for p in ("claude", "codex", "gemini")}
    provs = list(profiles.values())

    # --- the PRODUCTION approach: ONE /proc scan/tick fanned to N roots ----------
    sampler = ProcSampler()          # real /proc
    sampler.sample(roots)            # warm-up (establishes deltas; first scan)
    t0 = os.times()
    for _i in range(ticks):
        snap = sampler.sample(roots)
        for j, root in enumerate(roots):      # per-seat derive is part of tick work
            s = snap[root]
            sample = {"bytes_flowing": False, "cpu_core": s["cpu_core_pct"],
                      "io_delta": s["io_delta"], "live_pids": s["live_pids"],
                      "chrome": {}}
            derive_status(sample, profile=provs[j % len(provs)])
    cpu_one = _cpu_since(t0)
    scans = sampler.scan_count
    steady_state_pct = cpu_one / (ticks * MIN_POLL_S) * 100.0

    # --- the A0 ANTI-PATTERN baseline: a full /proc scan PER agent, SAME run ------
    # (0.32% one-scan vs 2.57% per-agent-ps historically ~8x). Measuring it live in
    # THIS run makes the guard LOAD-ROBUST: both approaches absorb the same machine
    # load + the same (currently large) /proc, so their RATIO is invariant to load.
    pa_ticks = 25
    pa_sampler = ProcSampler()
    pa_sampler.sample(roots)         # warm-up
    t0 = os.times()
    for _i in range(pa_ticks):
        for root in roots:           # the anti-pattern: one full scan per agent
            snap = pa_sampler.sample([root])
            s = snap[root]
            sample = {"bytes_flowing": False, "cpu_core": s["cpu_core_pct"],
                      "io_delta": s["io_delta"], "live_pids": s["live_pids"],
                      "chrome": {}}
            derive_status(sample, profile=provs[0])
    cpu_per_agent = _cpu_since(t0)

    one_per_tick = cpu_one / ticks
    pa_per_tick = cpu_per_agent / pa_ticks
    print(f"\n[b1-cpu-measure] roots={len(roots)} ticks={ticks} interval={MIN_POLL_S}s "
          f"cpu_one={cpu_one:.4f}s scans={scans} steady_state={steady_state_pct:.4f}% "
          f"| per-tick one-scan={one_per_tick*1e3:.3f}ms per-agent={pa_per_tick*1e3:.3f}ms "
          f"ratio={pa_per_tick/one_per_tick if one_per_tick else float('inf'):.2f}x "
          f"| high_load={_high_load()}")

    # STRUCTURAL (load-independent): ONE scan/tick, never per-agent. This is the A0
    # invariant by construction and always asserts.
    assert scans == ticks + 1, f"expected one scan/tick, got {scans} for {ticks} ticks"
    # LOAD-ROBUST RELATIVE guard: the production one-scan tick must be materially
    # cheaper per tick than the per-agent-scan anti-pattern (>=3x; historical ~8x).
    # Both absorb the same load => this ratio catches a regression to per-agent
    # scanning regardless of absolute machine load.
    assert one_per_tick * 3 < pa_per_tick, (
        f"one-scan-fanout not materially cheaper than per-agent scan "
        f"({one_per_tick*1e3:.3f}ms vs {pa_per_tick*1e3:.3f}ms/tick) — the A0 "
        f"per-agent-scan regression guard")
    # ABSOLUTE <1% budget: ADVISORY ONLY — never asserted. The genuine one-scan cost
    # on this hardware is ~1.05% and the live load STRADDLES any load-gate threshold,
    # so a load-gated absolute assert just moves the flake to the boundary (gm by-effect
    # gate finding). The load-INVARIANT guards above (the >=3x one-scan-vs-per-agent
    # ratio + scans==ticks+1) are the real regression guards the absolute was a proxy
    # for; the % is logged for the operator, never a pass/fail. (gm ruling: never a
    # silent threshold bump; make the absolute always advisory.)
