"""wal_tailer — the CAPTURE-ONLY WAL daemon for one seat (spec §7 step 1).

Wires the claude adapter + git/proc enrichers to the append-only store and runs
them on a CPU-disciplined cadence. This is the WHOLE of stage 1: WAL on, swaps
off. There is NO digest, NO state machine, NO swap, NO eviction, and NO
decide.py coupling here — by construction the only thing this process writes is
the lineage's WAL db.

CPU discipline (the operator-standing, hard):
  * offset-cursor incremental capture (the adapter re-reads no consumed bytes;
    the enrichers dedupe against their last sample) — a quiescent seat costs one
    cheap `git status` + a small dir scan per tick and nothing else;
  * poll floor MIN_POLL_S = 5s (run() refuses a tighter interval);
  * one process per seat, enforced by a non-blocking flock SeatLock.

Seat resolution (looking up the live seat's identity + source paths) lives in
the separate `run_capture` runner so this engine stays pure and provably inert.
"""
import fcntl
import os
import time

from .adapter_claude import ClaudeWalAdapter
from .enrichers import GitEnricher, ProcEnricher

MIN_POLL_S = 5.0


class WalTailer:
    def __init__(self, store, source_path, cwd, panes_dir, *,
                 lineage_root, sid, generation):
        self._store = store
        self._source = source_path
        self._adapter = ClaudeWalAdapter(store, lineage_root, generation)
        self._git = GitEnricher(store, lineage_root, generation, sid, cwd)
        self._proc = ProcEnricher(store, lineage_root, generation, sid,
                                  panes_dir)

    def tick(self):
        """One capture pass across all three sources. Returns #events appended.

        Order: transcript first (the primary WAL), then git side effects, then
        pane/proc telemetry. Each source advances its own cursor/last-sample, so
        a tick over a quiescent seat returns 0 and touches nothing.
        """
        n = self._adapter.tail(self._source)
        n += self._git.sample()
        n += self._proc.sample()
        return n

    def run(self, interval=MIN_POLL_S, should_stop=None, sleep=time.sleep):
        if interval < MIN_POLL_S:
            raise ValueError(
                f"poll interval {interval}s below CPU floor {MIN_POLL_S}s")
        should_stop = should_stop or (lambda: False)
        while not should_stop():
            self.tick()
            sleep(interval)


class SeatLock:
    """Non-blocking flock ensuring ONE tailer process per lineage (the per-seat
    half of the fleet supervisor cap). A second acquire() returns False rather
    than double-tailing the same stream."""

    def __init__(self, lineage_root, lock_dir):
        os.makedirs(lock_dir, exist_ok=True)
        self._path = os.path.join(lock_dir, f"wal-tailer-{lineage_root}.lock")
        self._fh = None

    def acquire(self):
        fh = open(self._path, "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh
        return True

    def release(self):
        if self._fh is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
