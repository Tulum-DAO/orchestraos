"""multiplexer — the multiplexed WAL tailer daemon (Build A core).

ONE process, N cursors, one liveness-lock. This EXTENDS the stage-1/2 WAL lane
(store.py, adapter_claude.py, adapter_codex.py, adapter_gemini.py, enrichers.py)
into a fleet-wide normalized capture. It is NOT a net-new daemon codebase — it is
the per-seat cursor manager + single-process supervisor over the existing
per-provider adapters. The durable/normalized/rotation-survival lane; Build B
(OS-telemetry surface) and Build B1 (real-time overlay) are consumers ON TOP.

Design decisions (from the commission):
  * MULTIPLEX: one daemon tails N seats via N per-seat cursors. Each seat keeps
    its existing per-lineage db (state/wal/<lineage_root>.db) — rotation-survival
    semantics are per-lineage — so "N cursors" = N stores fanned across ONE
    process, not N processes.
  * PROVIDER-AGNOSTIC dispatch: make_adapter() selects claude/codex/gemini;
    unknown runtime fails SAFE to claude (runtime_signatures precedent,
    the operator-approved apr_a72c10f8).
  * ONE liveness-lock (LivenessLock): pid-liveness-checked (regenerator.py
    _pid_alive pattern) so a Restart-after-SIGKILL never wedges on a stale lock.
  * COEXIST with per-seat WAL locks: register() acquires each seat's SeatLock;
    a seat already owned by a run_capture tailer is SKIPPED (never two writers
    on one seat's db).
  * E-BRAKE: honors the TELEMETRY_DISABLED kill-switch (DEC-1788479670) — a tick
    while it is set captures nothing. NOT BG_DISABLED: this durable tailer is a
    pure observer and telemetry-owned, so the arm kill (present indefinitely while
    the arm is un-tapped) must NOT dark the rotation-survival store; BG_DISABLED
    still gates the ARM (bg_state.is_armed), unchanged.

CPU discipline (the operator-standing, hard): this lane is EVENT/POLL-based over files +
one sqlite RO poll + a cheap git/pane sample per seat — it NEVER scans /proc
per-agent (A0 proved ps-fork-per-agent = FAIL). Poll floor MIN_POLL_S = 5s.

INERT: importing/constructing this module writes nothing; only tick()/run()
capture, and run() is not wired to any cron/systemd unit by this build (the
systemd User=shaw unit is the LAST step, installed by root after A/B/B1/C).
"""
import fcntl
import os
import time
import logging
from dataclasses import dataclass

from . import bg_state

_LOG = logging.getLogger(__name__)
from .adapter_claude import ClaudeWalAdapter
from .adapter_codex import CodexWalAdapter
from .adapter_gemini import GeminiWalAdapter
from .enrichers import GitEnricher, ProcEnricher
from .store import WalStore
from .wal_tailer import SeatLock

MIN_POLL_S = 5.0
_LOCK_NAME = "wal-multiplexer.lock"
# F2 CPU lever 2: the fleet-observer git-enriches at DURABLE-AUDIT cadence, not
# every 5s slow tick. git status on the shared agent-orchestra repo is ~170ms;
# at 60s (with per-cwd dedup) the whole observer git cost is ~0.35% of a core.
# The real-time status lane uses no git, so only file_mod/git audit freshness
# relaxes 5s->60s. Verified by effect (lock holders): the observer covers 29
# seats run_capture does NOT, so git cannot simply be removed here.
GIT_ENRICH_INTERVAL_S = 60.0


def _pid_alive(pid):
    """True if pid is a live process (regenerator.py pattern)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (ValueError, OverflowError):
        return False
    return True


def make_adapter(runtime, store, lineage_root, generation):
    """Provider-agnostic adapter dispatch. Unknown runtime fails SAFE to claude
    (the runtime_signatures unknown->claude precedent)."""
    if runtime == "codex":
        return CodexWalAdapter(store, lineage_root, generation)
    if runtime == "gemini":
        return GeminiWalAdapter(store, lineage_root, generation)
    return ClaudeWalAdapter(store, lineage_root, generation)


@dataclass
class MuxSeat:
    lineage_root: str
    runtime: str
    source_path: str           # jsonl (claude/codex) or db (gemini)
    sid: str
    generation: int
    cwd: str = None            # optional: enables the GitEnricher
    panes_dir: str = None      # optional: enables the ProcEnricher


class LivenessLock:
    """Single-instance guard for the ONE multiplexer process.

    Implemented with a non-blocking flock (like the module's own SeatLock). This
    delivers exactly the property the commission's regenerator._pid_alive pattern
    was chosen for — "restart-after-SIGKILL never wedges on a stale lock" — and
    delivers it MORE robustly: the kernel releases an flock automatically when the
    holder dies (SIGKILL, crash, reboot), so a stale lock can never wedge, AND it
    correctly refuses BOTH a second same-process instance and a live cross-process
    holder (a pure pid-file cannot refuse a recycled/reentrant pid). The owner pid
    is still written into the file for debuggability/observability.

    Deviation note (for the gm gate): the reference was the pid-liveness form; the
    flock form is strictly stronger for the stated single-instance + SIGKILL goal
    and is consistent with SeatLock already in this file. `_pid_alive` is retained
    for callers/observability."""

    def __init__(self, path):
        self._path = path
        self._fh = None

    def acquire(self):
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        fh = open(self._path, "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False   # a live holder (this or another process) owns it
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh
        return True

    def release(self):
        if self._fh is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


class _SeatCapture:
    """One seat's capture state: its store, per-provider adapter, optional
    enrichers, and the held per-seat SeatLock. Holds this seat's cursor(s) via
    the store's wal_cursors (keyed by source_path)."""

    def __init__(self, seat, store, seat_lock, run_cache=None):
        self.seat = seat
        self.store = store
        self.lock = seat_lock
        self.adapter = make_adapter(seat.runtime, store, seat.lineage_root,
                                    seat.generation)
        self.enrichers = []
        if seat.cwd:
            self.enrichers.append(GitEnricher(
                store, seat.lineage_root, seat.generation, seat.sid, seat.cwd,
                runtime=seat.runtime, run_cache=run_cache,
                min_interval_s=GIT_ENRICH_INTERVAL_S))
        if seat.panes_dir:
            self.enrichers.append(ProcEnricher(
                store, seat.lineage_root, seat.generation, seat.sid,
                seat.panes_dir, runtime=seat.runtime))

    def tick(self):
        n = self.adapter.tail(self.seat.source_path)
        for e in self.enrichers:
            n += e.sample()
        return n

    def close(self):
        try:
            self.store.close()
        finally:
            self.lock.release()


class MultiplexedTailer:
    def __init__(self, wal_dir, lock_dir, *, orchestra_dir=None, lock_path=None):
        self._wal_dir = wal_dir
        self._lock_dir = lock_dir
        self._orchestra_dir = orchestra_dir
        self._lock_path = lock_path or os.path.join(lock_dir, _LOCK_NAME)
        self._seats = {}   # lineage_root -> _SeatCapture
        self._liveness = None
        self.degraded_lineages = {}   # lineage_root -> reason for seats that raised this tick
        # F2 CPU: a per-tick, per-(cwd,args) git-exec cache shared by every seat's
        # GitEnricher. Seats sharing a cwd (the norm — dozens share the
        # agent-orchestra repo) then run `git status`/`rev-parse` ONCE per cwd per
        # tick instead of once per seat. Cleared at the top of every tick().
        self._enricher_run_cache = {}

    # --- registration -------------------------------------------------------

    def register(self, seat):
        """Register a seat for capture. Acquires the seat's per-seat SeatLock so
        the multiplexer NEVER double-writes a seat a run_capture tailer already
        owns — a locked seat is SKIPPED (returns False)."""
        os.makedirs(self._wal_dir, exist_ok=True)
        seat_lock = SeatLock(seat.lineage_root, self._wal_dir)
        if not seat_lock.acquire():
            return False   # another writer owns this seat's db; do not race it
        store = WalStore(os.path.join(self._wal_dir, f"{seat.lineage_root}.db"))
        self._seats[seat.lineage_root] = _SeatCapture(
            seat, store, seat_lock, run_cache=self._enricher_run_cache)
        return True

    # --- capture ------------------------------------------------------------

    def _telemetry_disabled(self):
        # DEC-1788479670: the durable tailer is a pure OBSERVER (RO poll + cheap
        # sample; rotates nothing) and is telemetry-owned (sole live caller =
        # telemetryd). It honors the TELEMETRY kill, NOT the arm kill BG_DISABLED —
        # gating it on BG_DISABLED (present indefinitely while the arm is un-tapped)
        # left the rotation-survival store permanently dark. BG_DISABLED still gates
        # the ARM (bg_state.is_armed), unchanged.
        return os.path.exists(bg_state._telemetry_disable_path(self._wal_dir))

    def tick(self):
        """One capture pass across ALL registered seats. Honors the
        TELEMETRY_DISABLED e-brake (returns 0 while set). Each seat advances its OWN
        cursor, so a tick over quiescent seats returns 0 and touches nothing."""
        self.degraded_lineages = {}
        if self._telemetry_disabled():
            return 0
        self._enricher_run_cache.clear()   # F2: one git exec per cwd for THIS tick
        n = 0
        # PER-SEAT CONTAINMENT (DEC-1788481319): a fleet observer must NEVER die of
        # one seat's bad data. One seat's adapter/store exception is logged + marked
        # degraded + skipped; the tick captures every other seat and NEVER raises.
        for root, cap in self._seats.items():
            try:
                n += cap.tick()
            except Exception as e:
                self.degraded_lineages[root] = "capture-error"
                _LOG.warning("telemetry mux: seat %s capture failed (%s); continuing",
                             root, type(e).__name__)
        return n

    def captured_runtimes(self):
        """The set of runtimes with at least one captured event — the
        cross-runtime matrix evidence (claude-only is not acceptable)."""
        seen = set()
        for cap in self._seats.values():
            for ev in cap.store.events():
                seen.add(ev["runtime"])
        return seen

    # --- supervised loop ----------------------------------------------------

    def run(self, interval=MIN_POLL_S, should_stop=None, sleep=time.sleep,
            max_iters=None):
        """The single-instance daemon loop. Acquires the ONE liveness-lock for
        its lifetime; returns 'lock-held' if a LIVE daemon already owns it.
        ``max_iters`` bounds the loop for tests. Returns the exit reason."""
        if interval < MIN_POLL_S:
            raise ValueError(
                f"poll interval {interval}s below CPU floor {MIN_POLL_S}s")
        self._liveness = LivenessLock(self._lock_path)
        if not self._liveness.acquire():
            return "lock-held"
        should_stop = should_stop or (lambda: False)
        iters = 0
        try:
            while not should_stop():
                self.tick()
                iters += 1
                if max_iters is not None and iters >= max_iters:
                    return "max-iters"
                sleep(interval)
            return "stopped"
        finally:
            self._liveness.release()

    def close(self):
        for cap in self._seats.values():
            cap.close()
        self._seats.clear()
        if self._liveness is not None:
            self._liveness.release()
            self._liveness = None
