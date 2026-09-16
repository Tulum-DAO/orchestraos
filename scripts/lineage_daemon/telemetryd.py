"""telemetryd — the telemetry-v2 dual-cadence wiring supervisor.

ONE process, ONE loop, TWO cadences (congruence DEC-1788465233):

  FAST (`FAST_POLL_S`, ~1s — the leg-(b) <=1s path): drain each seat's pipe-pane
    ring (telemetryd is the SOLE ring reader); on fresh bytes UPGRADE the seat to
    "streaming" and append the court-scrub-gated token delta. No /proc, no tmux
    subprocess => sub-second. FAST may ONLY upgrade to streaming; it never writes
    idle/computing from a bytes-only sample and never emits a partial seat set.

  SLOW (`SLOW_POLL_S`, >=5s CPU floor): ONE `ProcSampler` scan fanned to every
    seat (A0 one-scan-fanout) + full `derive_status` per seat (the authoritative
    classification for EVERY seat, incl. no-ring seats) + `AttachSweep.sweep()` +
    `MultiplexedTailer.tick()` (the durable WAL lane). SLOW derives `bytes_flowing`
    from the fast path's `last_bytes_ts` recency (BYTES_RECENCY_S >= SLOW_POLL_S),
    NEVER re-reading the destructive ring (no double-drain).

Both cadences write the WHOLE in-memory cache via `write_status_snapshot` (full-
replace semantics => a partial write would erase omitted seats). Cache entries are
FULL records {status, lineage_root, runtime} so a fast-only upgrade preserves the
lineage/runtime metadata the API surface needs.

E-BRAKES / single-instance: honors `state/wal/TELEMETRY_DISABLED` at loop-top (skips the
whole tick) and holds ONE flock `LivenessLock` (kernel auto-releases on SIGKILL, so
a `Restart=on-failure` never wedges on a stale lock).

INERT: importing/constructing writes nothing and attaches nothing; only `tick()`/
`run()` act, and `run()` is NOT wired to any cron/systemd unit by this build (the
systemd `User=shaw` unit is the LAST, root-installed step).
"""
import json
import logging
import os
import time

_LOG = logging.getLogger(__name__)

from .realtime.ansi import strip_ansi
from .realtime.provider_profiles import profile_for
from .realtime.snapshot import (MAX_DELTA_FRAMES, _deltas_dir, _safe_session,
                                write_status_snapshot)
from .realtime.novelty import classify_burst
from .realtime.status_deriver import derive_status
from .realtime.token_extractor import disposition_for_stream
from .wal import bg_state
from .wal.multiplexer import LivenessLock

FAST_POLL_S = 0.5           # sub-second FAST lane (the operator-ordered): status transitions
                            # (streaming/idle/offline) land <1s. Cheap pipe-pane
                            # drains + B1 novelty only — no subprocess, no /proc.
SLOW_POLL_S = 5.0           # == MIN_POLL_S CPU floor for the /proc-touching lane;
                            # the "computing" axis stays on this floor (may lag)
MIN_SLOW_POLL_S = 5.0

_STREAMING_SAMPLE = {"bytes_flowing": True}
# C1 corroboration window (DEC-1788493111 §9.2.2): recent NOVEL bytes within this
# span count a resident child's cpu/io as delegated work (the hookless
# blocked-on-resident-MCP mid-tool-call case); a working hook corroborates
# directly. Sized to cover a multi-minute pty-silent delegated MCP call.
CORROBORATION_S = 180.0


class TelemetryDaemon:
    def __init__(self, *, seats, mux, sweep, sampler, flag_store, snapshot_base,
                 wal_dir=None, lock_path=None,
                 fast_poll_s=FAST_POLL_S, slow_poll_s=SLOW_POLL_S,
                 bytes_recency_s=None, clock=time.time, sleep=time.sleep,
                 live_resolver=None, turn_resolver=None):
        if slow_poll_s < MIN_SLOW_POLL_S:
            raise ValueError(f"slow poll {slow_poll_s}s below CPU floor {MIN_SLOW_POLL_S}s")
        if bytes_recency_s is None:
            bytes_recency_s = slow_poll_s
        # precision #2: a recency shorter than the slow period would let a slow tick
        # self-downgrade a just-streamed seat before its next byte (a residual flap).
        if bytes_recency_s < slow_poll_s:
            raise ValueError("bytes_recency_s must be >= slow_poll_s")
        self.seats = list(seats)
        self.mux = mux
        self.sweep = sweep
        self.sampler = sampler
        self.flag_store = flag_store
        self.base = snapshot_base
        self.wal_dir = wal_dir
        self.lock_path = lock_path
        self.fast_poll_s = fast_poll_s
        self.slow_poll_s = slow_poll_s
        self.bytes_recency_s = bytes_recency_s
        self._clock = clock
        self._sleep = sleep
        # E1 (status-oscillation fix) — augment hooks with age_s = now - emission ts
        # so the deriver's recency veto can fire. DEFAULT OFF = classic byte-identical.
        #   E1_ARMED=1  -> emit the age-aware (E1) verdict live.
        #   E1_SHADOW=1 -> emit CLASSIC, but log every seat/tick where E1 would diverge
        #                  (gm gate condition 3; log-only, never changes live output).
        self._e1_armed = os.environ.get("E1_ARMED") == "1"
        self._e1_shadow = os.environ.get("E1_SHADOW") == "1"
        self._e1_shadow_log = (os.environ.get("E1_SHADOW_LOG")
                               or os.path.join(str(snapshot_base), "e1-shadow.jsonl")) \
            if self._e1_shadow else None
        # ONE batched per-SLOW-tick resolver killing the frozen-build staleness class:
        # root_pid goes stale when a non-always_on seat ROTATES (old pid dead -> false
        # offline_crashed); hooks go stale at turn-end (a build-time working hook
        # persists -> false stalled/waiting_permission). Both are re-resolved each slow
        # classify instead of trusting the fields frozen at build_seats. CRITICAL for
        # the campaign CPU budget: this is ONE call/tick over ALL seat sessions => one
        # `tmux list-panes -a` subprocess yielding BOTH pane_pid and pane_id, then hooks
        # are read directly from the pane_id's event file (no per-seat get_pane_id
        # subprocess — the naive per-seat path cost ~10x). None => fall back to the
        # frozen seat fields (back-compat; unit tests inject a fake).
        #   live_resolver(sessions:list[str]) -> {session: {"pid": int|None,
        #                                                    "hooks": dict|None}}
        self._live_resolver = live_resolver
        # E2 turn-completion resolver: (session, runtime, hooks) -> bool|None.
        # Consulted ONLY for a working-hook seat (the stalled candidate) each slow
        # tick — Claude emits no Stop on interrupt/kill, so a hook file can freeze at
        # working; the transcript says whether the turn actually COMPLETED (idle) or
        # is a genuine mid-tool hang (stalled). None => no signal, trust the hook.
        self._turn_resolver = turn_resolver
        self._status = {}                 # session -> full record {status,lineage_root,runtime}
        self._last_bytes_ts = {}          # session -> ts of last NOVEL-content bytes
        self._tails = {}                  # session -> bounded rolling content tail (B1 novelty)
        self._last_novel_ts = {}          # session -> ts of last novel bytes (C1 corroboration)
        self._seq = {}                    # session -> monotonically increasing delta seq
        self._delta_lines = {}            # session -> delta-log line count (amortized trim)
        self._degraded = {}               # session -> closed-code reason (runtime failures)
        self._last_slow = None            # None => the first tick forces a slow pass
        self._last_written_sig = None     # last status.json signature (skip no-op fast writes)

    # --- helpers -------------------------------------------------------------
    def _telemetry_disabled(self):
        # DEC-1788479670: telemetryd is a pure observer (reads /proc+pty, writes
        # ephemeral hot surfaces) — it rotates/swaps NOTHING, so it must NOT honor
        # the arm kill BG_DISABLED (present indefinitely while the arm is un-tapped;
        # honoring it made the whole daemon a no-op — Gate-2 blocker #2). Its own
        # in-band halt is state/wal/TELEMETRY_DISABLED. BG_DISABLED still gates the
        # ARM (bg_state.is_armed), unchanged.
        if not self.wal_dir:
            return False
        return os.path.exists(bg_state._telemetry_disable_path(self.wal_dir))

    def _resolve_live(self):
        """ONE batched resolve of every seat's live {pid, hooks} this slow tick (a
        single tmux list-panes for the whole fleet, then cheap per-seat file reads —
        NOT a subprocess per seat). Returns {session: {"pid":int|None,"hooks":dict|
        None}}; on a resolver-absent/error result callers fall back to the frozen
        seat fields."""
        if self._live_resolver is None:
            return {}
        try:
            return self._live_resolver([s.session for s in self.seats]) or {}
        except Exception as e:
            _LOG.warning("telemetry live resolve failed (%s); using frozen seat state",
                         type(e).__name__)
            return {}

    def _pick_pid(self, seat, live):
        """The live pid for `seat` from the batched resolve, else the frozen
        seat.root_pid. A rotated seat resolves to its NEW live pid (killing the
        post-rotation offline_crashed flap); a genuinely gone seat resolves to None
        and falls back to the frozen (now-dead) pid so it still reads offline — a
        real death is never masked. Never worse than trusting the frozen field."""
        entry = live.get(seat.session)
        pid = entry.get("pid") if entry else None
        return pid if pid is not None else seat.root_pid

    def _pick_hooks(self, seat, live):
        """The live hook for `seat` from the batched resolve (frozen stalled/
        waiting_permission fix). A seat WORKING at daemon-start keeps
        hooks.state='working' forever on the frozen Seat -> false stalled once the
        turn ends; the live hook file reads idle. A seat PRESENT in the batched
        result with hooks=None genuinely has no working hook (-> idle), so that live
        None is authoritative; only a seat ABSENT from the result (resolver absent /
        errored) falls back to the frozen seat.hooks (never worse than pre-fix)."""
        entry = live.get(seat.session)
        return entry.get("hooks") if entry else seat.hooks

    def _log_e1_divergence(self, seat, classic, e1, age_s, hook_ts, now):
        """E1_SHADOW: append one JSONL record where the E1 verdict differs from the
        live classic verdict — the empirical divergence log for gm's gate (cond 3).
        Best-effort; a log error never disturbs classification."""
        try:
            os.makedirs(os.path.dirname(self._e1_shadow_log), exist_ok=True)
            rec = {"ts": round(now, 3), "seat": seat.session, "runtime": seat.runtime,
                   "classic": classic, "e1": e1,
                   "age_s": round(age_s, 3) if age_s is not None else None,
                   "hook_ts": hook_ts,
                   # the safety flag gm asked to prove ZERO of: E1 suppressing a
                   # >=10s stall. True here would be a violation.
                   "suppressed_real_stall": bool(classic == "stalled" and e1 != "stalled"
                                                 and (age_s is None or age_s >= 10.0))}
            with open(self._e1_shadow_log, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
        except Exception:
            pass

    def _resolve_turn(self, seat, hooks):
        """turn_complete for `seat` from the transcript resolver — bool|None. Called
        ONLY for a working-hook seat (the sole case it changes the classification):
        a completed turn (True) means the working hook is stale-by-emission (missed
        Stop) -> idle; an open turn (False) is a genuine hang -> stalled; None (no
        resolver / read error / non-claude) trusts the hook. Never raises."""
        if self._turn_resolver is None:
            return None
        try:
            return self._turn_resolver(seat.session, seat.runtime, hooks)
        except Exception as e:
            _LOG.warning("telemetry turn resolve failed (%s); trusting hook",
                         type(e).__name__)
            return None

    def _record(self, seat, status):
        rec = {"status": status, "lineage_root": seat.lineage_root,
               "runtime": seat.runtime}
        # surface a degraded reason (body-free, closed code): a runtime failure for
        # this seat, else the build-time reason (e.g. "no-generation").
        deg = self._degraded.get(seat.session) or seat.degraded
        if deg:
            rec["degraded"] = deg
        return rec

    def _next_seq(self, session):
        self._seq[session] = self._seq.get(session, 0) + 1
        return self._seq[session]

    def _append_clean_delta(self, session, runtime, raw, lineage_root):
        """Court write-gate + cheap persistence. REUSES disposition_for_stream (the
        safety-critical boundary) exactly as append_delta_if_clean does — flagged/
        fail-closed/empty-lineage => ZERO body bytes — but persists via O_APPEND +
        amortized trim (the <1% CPU fix: append_delta's read-500-rewrite per seat
        per second measured 0.9%; this is ~0.12%)."""
        if not lineage_root:                        # BLOCKING-1: fail CLOSED
            return
        d = disposition_for_stream(runtime, raw, lineage_root=lineage_root,
                                   flag_store=self.flag_store)
        if d.get("stream_mode") != "clean" or not d.get("streamed"):
            return                                  # flagged / fail-closed => no write
        self._delta_write(session, strip_ansi(d.get("text", "")))

    def _delta_write(self, session, text):
        d = _deltas_dir(self.base)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, _safe_session(session) + ".log")
        if session not in self._delta_lines:        # lazy init the count (bound is real
            try:                                    # even if a prior log exists on disk)
                with open(path) as fh:
                    self._delta_lines[session] = sum(1 for ln in fh if ln.strip())
            except OSError:
                self._delta_lines[session] = 0
        frame = {"seq": self._next_seq(session), "ts": time.time(), "text": text}
        with open(path, "a") as fh:                 # O_APPEND: O(1) persistence
            fh.write(json.dumps(frame) + "\n")
        self._delta_lines[session] += 1
        if self._delta_lines[session] > 2 * MAX_DELTA_FRAMES:
            self._trim_delta(path, session)         # amortized drop-oldest

    def _trim_delta(self, path, session):
        try:
            with open(path) as fh:
                lines = [ln for ln in fh.read().splitlines() if ln]
        except OSError:
            return
        lines = lines[-MAX_DELTA_FRAMES:]
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        os.replace(tmp, path)                       # atomic; reader never sees a partial
        self._delta_lines[session] = len(lines)

    # --- cadences ------------------------------------------------------------
    def _fast(self, now):
        """Sole ring reader. Upgrade to streaming ONLY on NOVEL content (B1): a
        redraw / chrome tick / repaint re-emits known screen bytes with zero model
        activity and must NOT read streaming (the false-active root cause). Novel
        content also drives the token delta + the C1 corroboration timestamp.
        PER-SEAT CONTAINED: one seat's ring/classify/append error is logged +
        marked degraded + skipped — never crashes the tick."""
        for seat in self.seats:
            if seat.ring is None:
                continue
            try:
                raw = seat.ring.read_all()
                if not raw:
                    continue
                verdict, self._tails[seat.session] = classify_burst(
                    raw, self._tails.get(seat.session, ""),
                    profile=profile_for(seat.runtime))
                if verdict != "novel_content":
                    continue                      # redraw / chrome: not model output
                self._last_bytes_ts[seat.session] = now
                self._last_novel_ts[seat.session] = now
                self._status[seat.session] = self._record(
                    seat, derive_status(_STREAMING_SAMPLE,
                                        profile=profile_for(seat.runtime), hooks=seat.hooks))
                self._append_clean_delta(seat.session, seat.runtime, raw, seat.lineage_root)
            except Exception as e:
                self._degraded[seat.session] = "adapter-error"
                _LOG.warning("telemetry fast: seat %s failed (%s); continuing",
                             seat.session, type(e).__name__)

    def _slow(self, now):
        """Authoritative full classification for EVERY seat + the durable lane.
        PER-SEAT CONTAINED; the durable mux.tick is itself per-seat-contained."""
        try:
            self.sweep.sweep()
        except Exception as e:
            _LOG.warning("telemetry sweep failed (%s); continuing", type(e).__name__)
        # Re-resolve each seat's live {pid, hooks} THIS tick (never the frozen build-
        # time fields) in ONE batched call, then sample the resolved pids so a rotated
        # seat is scanned at its new live pid and does not falsely read offline_crashed
        # on the dead old pid.
        live = self._resolve_live()
        live_pids = {seat.session: self._pick_pid(seat, live) for seat in self.seats}
        try:
            proc = self.sampler.sample([live_pids[s.session] for s in self.seats])
        except Exception as e:
            _LOG.warning("telemetry /proc sample failed (%s); slow classify skipped",
                         type(e).__name__)
            proc = {}
        for seat in self.seats:
            try:
                ps = proc[live_pids[seat.session]]
                hooks = self._pick_hooks(seat, live)     # live hook, not frozen seat.hooks
                # E2: for a working hook, ask the transcript whether the turn is
                # actually complete (missed-Stop stale hook) or still open (real
                # hang). Only working hooks are consulted (the sole stalled candidate).
                hook_working = bool(hooks and hooks.get("state") == "working")
                turn_complete = self._resolve_turn(seat, hooks) if hook_working else None
                # a working hook whose turn has COMPLETED is stale-by-emission: it is
                # neither a working intent (derive_status vetoes it via turn_complete)
                # NOR a corroborator of resident CPU (else a background MCP child's
                # cpu would read the idle seat as computing).
                hook_working_live = hook_working and turn_complete is not True
                recent = (now - self._last_bytes_ts.get(seat.session, float("-inf"))) \
                    < self.bytes_recency_s
                # C1: attributed work = own CLI + worker children. Resident children
                # (MCP servers, plumbing, idle background monitors) count ONLY when
                # corroborated by turn-activity — recent novel bytes or a LIVE working
                # hook (delegated real work) — else excluded (a background MCP query
                # while the seat idles must NOT read computing).
                recent_novel = (now - self._last_novel_ts.get(
                    seat.session, float("-inf"))) < CORROBORATION_S
                corroborated = recent_novel or hook_working_live
                cpu_core = ps.get("own_cpu_core_pct", ps["cpu_core_pct"]) \
                    + ps.get("worker_cpu_core_pct", 0.0)
                io_delta = ps.get("own_io_delta", ps["io_delta"]) \
                    + ps.get("worker_io_delta", 0)
                if corroborated:
                    cpu_core += ps.get("resident_cpu_core_pct", 0.0)
                    io_delta += ps.get("resident_io_delta", 0)
                sample = {"bytes_flowing": recent, "cpu_core": cpu_core,
                          "io_delta": io_delta, "live_pids": ps["live_pids"],
                          "pane_gone": not ps["alive"], "chrome": {},
                          "turn_complete": turn_complete}
                prof = profile_for(seat.runtime)
                # E1: age-aware hooks (recency veto). Absent ts / mode OFF => classic.
                hooks_e1 = hooks
                if hooks and hooks.get("ts") is not None and (self._e1_armed or self._e1_shadow):
                    hooks_e1 = {**hooks, "age_s": now - hooks["ts"]}
                if self._e1_armed:
                    verdict = derive_status(sample, profile=prof, hooks=hooks_e1)
                elif self._e1_shadow:
                    verdict = derive_status(sample, profile=prof, hooks=hooks)   # emit CLASSIC
                    e1 = derive_status(sample, profile=prof, hooks=hooks_e1)
                    if e1 != verdict:
                        self._log_e1_divergence(seat, verdict, e1,
                                                hooks_e1.get("age_s"),
                                                hooks.get("ts") if hooks else None, now)
                else:
                    verdict = derive_status(sample, profile=prof, hooks=hooks)   # classic (default)
                self._status[seat.session] = self._record(seat, verdict)
                self._degraded.pop(seat.session, None)   # recovered -> clear runtime degrade
            except Exception as e:
                self._degraded[seat.session] = "sample-error"
                _LOG.warning("telemetry slow: seat %s failed (%s); continuing",
                             seat.session, type(e).__name__)
        captured = 0
        try:
            captured = self.mux.tick()
        except Exception as e:                          # belt-and-suspenders (mux self-contains)
            _LOG.warning("telemetry mux.tick failed (%s); continuing", type(e).__name__)
        # merge the durable lane's per-seat degrade (lineage_root -> session)
        deg_lin = getattr(self.mux, "degraded_lineages", None) or {}
        if deg_lin:
            lin2sess = {s.lineage_root: s.session for s in self.seats}
            for root, reason in deg_lin.items():
                sess = lin2sess.get(root)
                if sess:
                    self._degraded[sess] = reason
        # mass-degrade alarm: a daemon must not look healthy while capturing nothing
        if self.seats:
            ndeg = sum(1 for s in self.seats
                       if self._degraded.get(s.session) or s.degraded)
            if ndeg == len(self.seats):
                _LOG.warning("telemetry: ALL %d seats degraded (capturing nothing)", ndeg)
            elif ndeg and ndeg / len(self.seats) > 0.5:
                _LOG.warning("telemetry: %d/%d seats degraded", ndeg, len(self.seats))

    def tick(self, now=None):
        """One supervisor pass. FAST every tick; SLOW when due. Writes the WHOLE
        cache once. Returns {skipped:'telemetry_disabled'} while the e-brake is set."""
        now = self._clock() if now is None else now
        if self._telemetry_disabled():
            return {"skipped": "telemetry_disabled"}
        self._fast(now)                   # fast first: records recency the slow pass reads
        did_slow = False
        if self._last_slow is None or (now - self._last_slow) >= self.slow_poll_s:
            self._last_slow = now
            self._slow(now)
            did_slow = True
        # Sub-second FAST (the operator amend) writes status.json every 0.5s. Writing the
        # WHOLE cache unconditionally at 2Hz over-costs CPU at fleet scale, so a
        # FAST tick writes ONLY when a status/degraded transition actually
        # occurred (the sub-second delivery a viewer cares about); the SLOW tick
        # ALWAYS writes (the <=5s ts heartbeat that keeps status.json fresh for the
        # staleness contract). Net: sub-second on change, no idle-fleet write churn.
        sig = tuple(sorted(
            (s, r.get("status"), r.get("degraded")) for s, r in self._status.items()))
        if did_slow or sig != self._last_written_sig:
            try:
                write_status_snapshot(self.base, self._status)
                self._last_written_sig = sig
            except Exception as e:                      # a disk-full/IO error must not crash
                _LOG.warning("telemetry: status snapshot write failed (%s); continuing",
                             type(e).__name__)
        return {"slow": did_slow, "seats": len(self._status)}

    # --- supervised loop -----------------------------------------------------
    def run(self, *, should_stop=None, max_iters=None):
        """INERT supervised loop under a single flock LivenessLock. Returns
        'lock-held' if a live telemetryd already owns the lock."""
        if self.fast_poll_s <= 0:
            raise ValueError("fast poll must be positive")
        lock = LivenessLock(self.lock_path) if self.lock_path else None
        if lock is not None and not lock.acquire():
            return "lock-held"
        should_stop = should_stop or (lambda: False)
        iters = 0
        try:
            while not should_stop():
                self.tick(self._clock())
                iters += 1
                if max_iters is not None and iters >= max_iters:
                    return "max-iters"
                self._sleep(self.fast_poll_s)
            return "stopped"
        finally:
            if lock is not None:
                lock.release()


def main(argv=None):  # pragma: no cover — the systemd entrypoint (live wiring)
    """`python -m scripts.lineage_daemon.telemetryd`. Builds the live seat set and
    runs the supervisor. Imported lazily so the module stays import-safe/INERT."""
    import signal
    from .seat_builder import build_live_daemon
    daemon = build_live_daemon()
    stop = {"v": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("v", True))
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("v", True))
    return daemon.run(should_stop=lambda: stop["v"])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(0 if main() else 1)
