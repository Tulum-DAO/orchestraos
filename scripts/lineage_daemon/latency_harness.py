"""Per-provider end-to-end latency harness (leg b).

Measures the pty-byte -> deriver -> status.json (streaming) path for EACH provider
(claude + codex + gemini, incl. the codex bytes-primary axis). Two modes:

  measure_processing_latency(provider) — deterministic: the DERIVER processing
    time for one fast tick that observes a byte and flips the seat to streaming.
    This is the term the wiring owns; the only other term is the fixed poll
    interval FAST_POLL_S(<=1s), so end-to-end status latency stays <=1s.

  main() — the live poll-bounded run: drives a real telemetryd loop at FAST_POLL_S,
    writes a byte at a random phase, and measures wall-clock byte->streaming. Emits
    the per-provider evidence table (the +SSE leg on top is Build B's poll, <=1s).
"""
import os
import time

from .pane_sink_tailer import PaneSinkTailer
from .realtime.daemon import Seat
from .realtime.snapshot import read_status_snapshot
from .telemetryd import TelemetryDaemon, FAST_POLL_S

PROVIDERS = ("claude", "codex", "gemini")

_IDLE_PROC = {"cpu_core_pct": 0.0, "io_delta": 0, "live_pids": 1, "alive": True}


class _IdleSampler:
    scan_count = 0
    def sample(self, roots):
        self.scan_count += 1
        return {p: _IDLE_PROC for p in roots}


class _Noop:
    def sweep(self):
        return {"attached": [], "errors": {}, "pruned": []}
    def tick(self):
        return 0


class _CleanFlags:
    def status(self, lineage_root):
        return (False, True)              # readable store, lineage clean


def _daemon_for(provider, base):
    sink_dir = os.path.join(base, "panes")
    os.makedirs(sink_dir, exist_ok=True)
    sink = os.path.join(sink_dir, provider + ".pipe")
    open(sink, "wb").close()
    seat = Seat(session=provider, lineage_root="clean-" + provider, runtime=provider,
                root_pid=1, ring=PaneSinkTailer(sink), hooks=None)
    d = TelemetryDaemon(seats=[seat], mux=_Noop(), sweep=_Noop(), sampler=_IdleSampler(),
                        flag_store=_CleanFlags(), snapshot_base=base, wal_dir=None,
                        lock_path=os.path.join(base, "t.lock"))
    return d, sink


def _is_streaming(base, session):
    snap = read_status_snapshot(base)
    if not snap:
        return False
    seat = (snap.get("seats") or {}).get(session)
    return bool(seat) and seat.get("status") == "streaming"


def measure_processing_latency(provider, *, base):
    """Deterministic: measure the deriver processing time for the byte->streaming
    fast tick. Returns {provider, processing_s, became_streaming}."""
    d, sink = _daemon_for(provider, base)
    t = 0.0
    d.tick(now=t)                              # slow tick #1: classify idle
    assert not _is_streaming(base, provider)
    with open(sink, "ab") as fh:               # a pty byte arrives (O_APPEND, like cat)
        fh.write(b"model output token")
    t = 1.0                                    # a fast tick (no slow due)
    t0 = time.perf_counter()
    d.tick(now=t)
    processing_s = time.perf_counter() - t0
    return {"provider": provider, "processing_s": processing_s,
            "became_streaming": _is_streaming(base, provider)}


def measure_e2e_poll_bounded(provider, *, base, poll_s=FAST_POLL_S, iters=20):  # pragma: no cover
    """Live poll-bounded byte->streaming wall-clock (the REAL end-to-end number,
    minus Build B's SSE poll). A background thread ticks the daemon on the true
    wall-clock at poll_s; the main thread writes a byte at a random phase and
    times the flip to streaming. Latency ~ uniform[processing, poll_s]."""
    import random
    import threading
    d, sink = _daemon_for(provider, base)
    d.tick(now=time.time())                    # prime (idle)
    stop = {"v": False}

    def _ticker():
        while not stop["v"]:
            d.tick(now=time.time())
            time.sleep(poll_s)

    th = threading.Thread(target=_ticker, daemon=True)
    th.start()
    lat = []
    try:
        for _ in range(iters):
            time.sleep(random.uniform(0, poll_s))   # arrive at a random phase
            PaneSinkTailer(sink).read_all()          # ensure a clean edge
            t0 = time.time()
            with open(sink, "ab") as fh:
                fh.write(b"tok")
            while not _is_streaming(base, provider):
                time.sleep(0.002)                    # observe the ticker's effect
            lat.append(time.time() - t0)
            time.sleep(poll_s * 1.2)                 # let it settle before next edge
    finally:
        stop["v"] = True
        th.join(timeout=2)
    lat.sort()
    p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))]
    return {"provider": provider, "p50": lat[len(lat) // 2], "p95": p95,
            "max": lat[-1], "n": len(lat)}


def main(argv=None):  # pragma: no cover
    import json
    import tempfile
    out = {}
    with tempfile.TemporaryDirectory() as td:
        for p in PROVIDERS:
            proc = measure_processing_latency(p, base=os.path.join(td, "proc", p))
            e2e = measure_e2e_poll_bounded(p, base=os.path.join(td, "e2e", p))
            out[p] = {"processing_s": round(proc["processing_s"], 6),
                      "e2e_p50_s": round(e2e["p50"], 4), "e2e_p95_s": round(e2e["p95"], 4),
                      "e2e_max_s": round(e2e["max"], 4),
                      "streaming_ok": proc["became_streaming"], "poll_s": FAST_POLL_S,
                      "sub_second_ok": (e2e["p95"] < 1.0)}
    print(json.dumps({"schema": "telemetry-latency/v1", "poll_s": FAST_POLL_S,
                      "providers": out}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
