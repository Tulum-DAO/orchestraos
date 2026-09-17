"""RED tests for telemetryd — the dual-cadence wiring supervisor.

Design ratified by congruence DEC-1788465233 (with two precision notes folded in):
  * ONE process, ONE loop, TWO cadences: FAST (bytes/token/streaming, no /proc) +
    SLOW (>=5s: /proc sample + full derive_status + attach-sweep + WAL mux.tick).
  * ONE in-memory full-fleet cache; every write emits the WHOLE cache.
  * SLOW owns classification for every seat; FAST may ONLY upgrade to "streaming".
  * FAST is the sole ring reader; SLOW derives bytes_flowing from last_bytes_ts
    recency (BYTES_RECENCY_S >= SLOW_POLL_S), never re-reading the ring.
  * cache entries are FULL records {status, lineage_root, runtime} (precision #1).
  * last_slow init 0 => first tick forces a full slow classification (precision #2).
"""
import os

import pytest

from .realtime.daemon import Seat
from .realtime.snapshot import read_status_snapshot, read_delta_frames
from .telemetryd import TelemetryDaemon, FAST_POLL_S, SLOW_POLL_S


class FakeRing:
    """A pipe-pane ring stand-in: read_all() yields queued bytes ONCE (drain)."""
    def __init__(self):
        self._q = []
    def feed(self, b):
        self._q.append(b)
    def read_all(self):
        if not self._q:
            return b""
        out = b"".join(self._q)
        self._q = []
        return out


class FakeSweep:
    def __init__(self):
        self.calls = 0
    def sweep(self):
        self.calls += 1
        return {"attached": [], "errors": {}, "pruned": []}


class FakeMux:
    def __init__(self):
        self.calls = 0
    def tick(self):
        self.calls += 1
        return 0


class FakeSampler:
    """Returns a proc reading per root pid; mirrors ProcSampler.sample() keys."""
    def __init__(self, table):
        self.table = table          # {pid: {cpu_core_pct, io_delta, live_pids, alive}}
        self.scan_count = 0
    def sample(self, roots):
        self.scan_count += 1
        return {p: self.table[p] for p in roots}


class FakeFlags:
    def __init__(self, flagged=(), readable=True):
        self._flagged = set(flagged)
        self._readable = readable
    def status(self, lineage_root):
        return (lineage_root in self._flagged, self._readable)


IDLE_PROC = {"cpu_core_pct": 0.0, "io_delta": 0, "live_pids": 1, "alive": True}


def _seat(session, root_pid, ring=None, runtime="claude", lineage="lin-" ):
    return Seat(session=session, lineage_root=lineage + session, runtime=runtime,
                root_pid=root_pid, ring=ring, hooks=None)


def _mk(tmp, seats, sampler, *, sweep=None, mux=None, flags=None,
        fast=FAST_POLL_S, slow=SLOW_POLL_S, recency=None, clock=None,
        live_resolver=None, turn_resolver=None):
    return TelemetryDaemon(
        seats=seats, mux=mux or FakeMux(), sweep=sweep or FakeSweep(),
        sampler=sampler, flag_store=flags or FakeFlags(), snapshot_base=str(tmp),
        wal_dir=None, lock_path=os.path.join(str(tmp), "telemetryd.lock"),
        fast_poll_s=fast, slow_poll_s=slow, bytes_recency_s=recency,
        clock=clock or (lambda: 0.0),
        live_resolver=live_resolver, turn_resolver=turn_resolver)


def _live(pid=None, hooks=None):
    """One seat's batched-resolve entry {pid, hooks}."""
    return {"pid": pid, "hooks": hooks}


def test_first_tick_forces_slow_classification_of_all_seats(tmp_path):
    # last_slow init 0 => the very first tick classifies every seat (no gap).
    seats = [_seat("gm", 100), _seat("codex-dev-1", 200, runtime="codex")]
    sampler = FakeSampler({100: IDLE_PROC, 200: IDLE_PROC})
    d = _mk(tmp_path, seats, sampler, clock=lambda: 0.0)
    d.tick(now=0.0)
    snap = read_status_snapshot(str(tmp_path))
    assert set(snap["seats"]) == {"gm", "codex-dev-1"}
    assert snap["seats"]["gm"]["status"] == "idle"
    assert sampler.scan_count == 1                 # slow ran on the first tick


def test_fast_upgrade_sets_streaming_and_preserves_lineage_and_runtime(tmp_path):
    # precision #1: a fast-path-only upgrade must keep lineage_root + runtime.
    ring = FakeRing()
    seats = [_seat("gm", 100, ring=ring, runtime="claude")]
    sampler = FakeSampler({100: IDLE_PROC})
    d = _mk(tmp_path, seats, sampler, clock=lambda: 0.0)
    d.tick(now=0.0)                                 # slow: idle
    ring.feed(b"hello world tokens")
    d.tick(now=1.0)                                 # fast: bytes -> streaming
    snap = read_status_snapshot(str(tmp_path))
    seat = snap["seats"]["gm"]
    assert seat["status"] == "streaming"
    assert seat["lineage_root"] == "lin-gm"
    assert seat["runtime"] == "claude"


def test_fast_tick_without_bytes_does_not_clobber_slow_classification(tmp_path):
    # a computing seat (io_delta high) must NOT be flipped to idle by a bytes-less
    # fast tick — fast is upgrade-only.
    computing = {"cpu_core_pct": 0.0, "io_delta": 999999, "live_pids": 2, "alive": True}
    ring = FakeRing()
    seats = [_seat("codex-dev-1", 200, ring=ring, runtime="codex")]
    sampler = FakeSampler({200: computing})
    d = _mk(tmp_path, seats, sampler, clock=lambda: 0.0)
    d.tick(now=0.0)                                 # slow: computing (io primary)
    assert read_status_snapshot(str(tmp_path))["seats"]["codex-dev-1"]["status"] == "computing"
    d.tick(now=1.0)                                 # fast, no bytes: must stay computing
    assert read_status_snapshot(str(tmp_path))["seats"]["codex-dev-1"]["status"] == "computing"


def test_slow_uses_bytes_recency_not_ring_reread(tmp_path):
    # fast drains the ring; the next slow tick must see streaming via recency,
    # NOT downgrade because the ring is now empty (no double-drain).
    ring = FakeRing()
    seats = [_seat("gm", 100, ring=ring, runtime="claude")]
    sampler = FakeSampler({100: IDLE_PROC})
    d = _mk(tmp_path, seats, sampler, slow=SLOW_POLL_S, recency=SLOW_POLL_S, clock=lambda: 0.0)
    d.tick(now=0.0)                                 # slow: idle
    ring.feed(b"streaming bytes")
    d.tick(now=1.0)                                 # fast: drains ring -> streaming
    # next slow tick at >= SLOW_POLL_S: ring empty, but recency still fresh
    d.tick(now=SLOW_POLL_S)
    assert read_status_snapshot(str(tmp_path))["seats"]["gm"]["status"] == "streaming"


def test_whole_cache_written_no_seat_erased_by_fast_tick(tmp_path):
    # a no-ring seat must persist across fast ticks (fast writes the WHOLE cache).
    ring = FakeRing()
    seats = [_seat("gm", 100, ring=ring), _seat("noring", 300)]
    sampler = FakeSampler({100: IDLE_PROC, 300: IDLE_PROC})
    d = _mk(tmp_path, seats, sampler, clock=lambda: 0.0)
    d.tick(now=0.0)                                 # slow: both classified
    ring.feed(b"x")
    d.tick(now=1.0)                                 # fast: gm->streaming; noring must remain
    snap = read_status_snapshot(str(tmp_path))
    assert set(snap["seats"]) == {"gm", "noring"}
    assert snap["seats"]["noring"]["status"] == "idle"


def test_slow_work_throttled_to_slow_poll(tmp_path):
    seats = [_seat("gm", 100)]
    sampler = FakeSampler({100: IDLE_PROC})
    sweep, mux = FakeSweep(), FakeMux()
    d = _mk(tmp_path, seats, sampler, sweep=sweep, mux=mux, clock=lambda: 0.0)
    d.tick(now=0.0)                                 # slow #1 (forced)
    d.tick(now=1.0)                                 # fast only
    d.tick(now=2.0)                                 # fast only
    assert sampler.scan_count == 1 and sweep.calls == 1 and mux.calls == 1
    d.tick(now=SLOW_POLL_S)                         # slow #2
    assert sampler.scan_count == 2 and sweep.calls == 2 and mux.calls == 2


def test_bg_disabled_alone_does_NOT_stop_telemetry(tmp_path):
    # Gate-2 blocker #2: BG_DISABLED is present-by-design (arm protection). The
    # B1 real-time lane is a pure observer, NOT swap-adjacent -> it MUST run and
    # write status.json even while BG_DISABLED is present. (DEC-1788479670)
    wal = tmp_path / "wal"
    wal.mkdir()
    (wal / "BG_DISABLED").write_text("")           # arm e-brake present (indefinitely)
    ring = FakeRing()
    ring.feed(b"streaming tokens")
    seats = [_seat("gm", 100, ring=ring)]
    sampler = FakeSampler({100: IDLE_PROC})
    sweep, mux = FakeSweep(), FakeMux()
    d = TelemetryDaemon(
        seats=seats, mux=mux, sweep=sweep, sampler=sampler, flag_store=FakeFlags(),
        snapshot_base=str(tmp_path), wal_dir=str(wal),
        lock_path=str(tmp_path / "t.lock"), clock=lambda: 0.0)
    out = d.tick(now=0.0)
    assert "skipped" not in out                     # NOT halted by BG_DISABLED
    snap = read_status_snapshot(str(tmp_path))
    assert snap is not None and snap["seats"]["gm"]["status"] == "streaming"
    assert sweep.calls == 1 and sampler.scan_count == 1   # both lanes ran


def test_telemetry_disabled_halts_the_daemon(tmp_path):
    # telemetryd's OWN kill file (state/wal/TELEMETRY_DISABLED) is the in-band halt.
    wal = tmp_path / "wal"
    wal.mkdir()
    (wal / "TELEMETRY_DISABLED").write_text("")     # the telemetry kill
    seats = [_seat("gm", 100)]
    sampler = FakeSampler({100: IDLE_PROC})
    sweep, mux = FakeSweep(), FakeMux()
    d = TelemetryDaemon(
        seats=seats, mux=mux, sweep=sweep, sampler=sampler, flag_store=FakeFlags(),
        snapshot_base=str(tmp_path), wal_dir=str(wal),
        lock_path=str(tmp_path / "t.lock"), clock=lambda: 0.0)
    out = d.tick(now=0.0)
    assert out == {"skipped": "telemetry_disabled"}
    assert sampler.scan_count == 0 and sweep.calls == 0 and mux.calls == 0
    assert read_status_snapshot(str(tmp_path)) is None   # nothing written


def test_bytes_recency_must_be_ge_slow_poll(tmp_path):
    seats = [_seat("gm", 100)]
    sampler = FakeSampler({100: IDLE_PROC})
    with pytest.raises(ValueError):
        _mk(tmp_path, seats, sampler, slow=5.0, recency=1.0)   # 1 < 5 -> refuse


def test_run_refuses_second_instance_while_liveness_lock_held(tmp_path):
    # leg (c) single-instance: the flock LivenessLock (reused from multiplexer,
    # already SIGKILL-tested there) gives telemetryd its single-instance guard.
    from .wal.multiplexer import LivenessLock
    lp = str(tmp_path / "t.lock")
    holder = LivenessLock(lp)
    assert holder.acquire()
    seats = [_seat("gm", 100)]
    d = TelemetryDaemon(
        seats=seats, mux=FakeMux(), sweep=FakeSweep(), sampler=FakeSampler({100: IDLE_PROC}),
        flag_store=FakeFlags(), snapshot_base=str(tmp_path), wal_dir=None,
        lock_path=lp, clock=lambda: 0.0)
    assert d.run(max_iters=1) == "lock-held"       # refused while held
    holder.release()
    assert d.run(max_iters=1) == "max-iters"        # acquires once free


def test_flagged_lineage_appends_zero_delta_bytes(tmp_path):
    ring = FakeRing()
    seats = [_seat("gm", 100, ring=ring, runtime="claude")]
    sampler = FakeSampler({100: IDLE_PROC})
    flags = FakeFlags(flagged={"lin-gm"})          # gm's lineage is court-flagged
    d = _mk(tmp_path, seats, sampler, flags=flags, clock=lambda: 0.0)
    d.tick(now=0.0)
    ring.feed(b"contaminated model voice")
    d.tick(now=1.0)                                 # fast: streaming status ok, but...
    assert read_delta_frames(str(tmp_path), "gm") == []   # ZERO body bytes written


def test_clean_lineage_appends_delta_frames_and_stays_bounded(tmp_path):
    # clean tokens ARE persisted (through the reused court gate) AND the delta log
    # stays bounded under sustained streaming (amortized trim; the <1% CPU fix must
    # not stop writing clean frames or let the file grow without bound).
    ring = FakeRing()
    seats = [_seat("gm", 100, ring=ring, runtime="claude")]
    d = _mk(tmp_path, seats, FakeSampler({100: IDLE_PROC}), clock=lambda: 0.0)
    d.tick(now=0.0)
    for i in range(1, 40):                          # many streaming fast ticks
        ring.feed(b"clean token chunk %d" % i)
        d.tick(now=float(i))
    frames = read_delta_frames(str(tmp_path), "gm")
    assert len(frames) > 0                           # clean frames persisted
    # bounded: never grows without limit under sustained streaming
    import os
    from .realtime.snapshot import MAX_DELTA_FRAMES, _deltas_dir, _safe_session
    path = os.path.join(_deltas_dir(str(tmp_path)), _safe_session("gm") + ".log")
    with open(path) as fh:
        nlines = sum(1 for ln in fh if ln.strip())
    assert nlines <= 2 * MAX_DELTA_FRAMES


class _BoomRing:
    def read_all(self):
        raise RuntimeError("ring exploded")


def test_one_bad_seat_does_not_kill_telemetryd_and_is_degraded(tmp_path):
    good = _seat("good", 100, ring=FakeRing())
    good.ring.feed(b"tokens")
    bad = _seat("bad", 200, ring=_BoomRing())
    sampler = FakeSampler({100: IDLE_PROC, 200: IDLE_PROC})
    d = _mk(tmp_path, [good, bad], sampler, clock=lambda: 0.0)
    out = d.tick(now=0.0)                                # must NOT raise
    assert "skipped" not in out
    snap = read_status_snapshot(str(tmp_path))
    assert snap is not None
    assert set(snap["seats"]) == {"good", "bad"}        # both surfaced, none erased
    assert snap["seats"]["bad"].get("degraded")         # bad seat marked degraded
    assert snap["seats"]["good"].get("degraded") in (None, "", ...) or "degraded" not in snap["seats"]["good"]


def test_build_time_degraded_seat_surfaced_in_status_json(tmp_path):
    # a seat the seat-builder marked degraded (e.g. no-generation) surfaces in status.json
    s = _seat("nogen", 100)
    s.degraded = "no-generation"
    d = _mk(tmp_path, [s], FakeSampler({100: IDLE_PROC}), clock=lambda: 0.0)
    d.tick(now=0.0)
    snap = read_status_snapshot(str(tmp_path))
    assert snap["seats"]["nogen"].get("degraded") == "no-generation"


# --- per-tick refresh: re-resolve root_pid + hooks each SLOW tick, never frozen ---
# (the frozen-state class: derive_status inputs captured at build_seats go stale on
# rotation (pid -> false offline_crashed) and at turn-end (hooks -> false stalled).)

DEAD_PROC = {"cpu_core_pct": 0.0, "io_delta": 0, "live_pids": 0, "alive": False}


def test_slow_reresolves_root_pid_killing_post_rotation_offline_flap(tmp_path):
    # A non-always_on seat BUILT with pid P1 rotates to a new pid P2: P1 is now dead
    # (gone from /proc), P2 is live. Classifying on the FROZEN build-time pid reads
    # offline_crashed (P1 dead); re-resolving to the live P2 each slow tick must read
    # the live status (idle), killing the false offline_crashed flap.
    seat = _seat("rotator", 100)                       # built with P1 = 100
    sampler = FakeSampler({100: DEAD_PROC, 200: IDLE_PROC})   # 100 dead, 200 alive
    d = _mk(tmp_path, [seat], sampler, clock=lambda: 0.0,
            live_resolver=lambda sessions: {"rotator": _live(pid=200)})   # rotated to 200
    d.tick(now=0.0)
    assert read_status_snapshot(str(tmp_path))["seats"]["rotator"]["status"] == "idle"


def test_slow_root_pid_none_resolution_falls_back_to_frozen_offline(tmp_path):
    # If the resolver reports pid None (the seat's pane is genuinely gone), fall back
    # to the frozen pid so a truly-dead seat still reads offline_crashed (never masked).
    seat = _seat("gone", 100)
    sampler = FakeSampler({100: DEAD_PROC})
    d = _mk(tmp_path, [seat], sampler, clock=lambda: 0.0,
            live_resolver=lambda sessions: {"gone": _live(pid=None)})   # pane gone
    d.tick(now=0.0)
    assert read_status_snapshot(str(tmp_path))["seats"]["gone"]["status"] == "offline_crashed"


def test_slow_reresolves_hooks_killing_frozen_stalled(tmp_path):
    # A seat BUILT with a working hook whose hook FILE later reads Stop/idle. The
    # frozen build-time working hook + zero activity reads stalled; re-resolving the
    # hook per slow tick reads the live idle hook -> idle (not stalled).
    seat = _seat("worker", 100)
    seat.hooks = {"state": "working"}                  # frozen build-time working hook
    sampler = FakeSampler({100: IDLE_PROC})            # zero bytes / zero compute
    d = _mk(tmp_path, [seat], sampler, clock=lambda: 0.0,
            live_resolver=lambda sessions: {"worker": _live(pid=100, hooks={"state": "idle"})})
    d.tick(now=0.0)
    assert read_status_snapshot(str(tmp_path))["seats"]["worker"]["status"] == "idle"


def test_slow_live_hooks_none_when_present_means_idle_not_frozen_stalled(tmp_path):
    # A seat PRESENT in the batched result with hooks=None (the file cleared / no
    # working hook) is authoritative live truth -> idle; the frozen working hook must
    # NOT leak back in. (This is the exact false-STALLED live case gm caught.)
    seat = _seat("cleared", 100)
    seat.hooks = {"state": "working"}                  # frozen working
    sampler = FakeSampler({100: IDLE_PROC})
    d = _mk(tmp_path, [seat], sampler, clock=lambda: 0.0,
            live_resolver=lambda sessions: {"cleared": _live(pid=100, hooks=None)})
    d.tick(now=0.0)
    assert read_status_snapshot(str(tmp_path))["seats"]["cleared"]["status"] == "idle"


def test_slow_reresolves_hooks_clearing_frozen_waiting_permission(tmp_path):
    # Same frozen-staleness class for waiting_permission: a build-time
    # waiting_permission hook that the file later clears must not persist.
    seat = _seat("perm", 100)
    seat.hooks = {"state": "waiting_permission"}       # frozen
    sampler = FakeSampler({100: IDLE_PROC})
    d = _mk(tmp_path, [seat], sampler, clock=lambda: 0.0,
            live_resolver=lambda sessions: {"perm": _live(pid=100, hooks={"state": "idle"})})
    d.tick(now=0.0)
    assert read_status_snapshot(str(tmp_path))["seats"]["perm"]["status"] == "idle"


def test_slow_genuine_live_working_hook_still_stalled_when_zero_activity(tmp_path):
    # Re-resolution runs BOTH directions: a seat whose FROZEN hook was idle but whose
    # LIVE hook now reads working, with zero bytes/compute, IS a true stall (hung
    # tool) and must classify stalled — re-resolution must not mask a genuine stall.
    seat = _seat("hung", 100)
    seat.hooks = {"state": "idle"}                     # frozen was idle...
    sampler = FakeSampler({100: IDLE_PROC})
    d = _mk(tmp_path, [seat], sampler, clock=lambda: 0.0,
            live_resolver=lambda sessions: {"hung": _live(pid=100, hooks={"state": "working"})})
    d.tick(now=0.0)
    assert read_status_snapshot(str(tmp_path))["seats"]["hung"]["status"] == "stalled"


def test_no_resolver_falls_back_to_frozen_seat_fields(tmp_path):
    # Back-compat: with no resolver injected the daemon uses the frozen seat pid +
    # hooks exactly as before (a working frozen hook + zero activity -> stalled).
    seat = _seat("legacy", 100)
    seat.hooks = {"state": "working"}
    sampler = FakeSampler({100: IDLE_PROC})
    d = _mk(tmp_path, [seat], sampler, clock=lambda: 0.0)   # no resolver
    d.tick(now=0.0)
    assert read_status_snapshot(str(tmp_path))["seats"]["legacy"]["status"] == "stalled"


def test_live_resolver_error_falls_back_to_frozen_never_crashes(tmp_path):
    # A batched resolver that raises must not crash the tick; every seat falls back to
    # its frozen pid + hooks (no worse than pre-fix), so a live frozen pid classifies
    # normally and a frozen working hook still reads stalled.
    seat = _seat("gm", 100)
    seat.hooks = {"state": "working"}
    sampler = FakeSampler({100: IDLE_PROC})

    def boom(sessions):
        raise RuntimeError("tmux list-panes exploded")

    d = _mk(tmp_path, [seat], sampler, clock=lambda: 0.0, live_resolver=boom)
    out = d.tick(now=0.0)                               # must NOT raise
    assert "skipped" not in out
    assert read_status_snapshot(str(tmp_path))["seats"]["gm"]["status"] == "stalled"


def test_live_resolver_omitting_a_seat_falls_back_to_frozen(tmp_path):
    # The batched map need not contain every seat (a session not in list-panes):
    # an omitted seat falls back to its frozen pid + hooks, not a KeyError.
    seat = _seat("present", 100)
    other = _seat("omitted", 300)
    other.hooks = {"state": "working"}                 # frozen working on the omitted seat
    sampler = FakeSampler({100: IDLE_PROC, 300: IDLE_PROC})
    d = _mk(tmp_path, [seat, other], sampler, clock=lambda: 0.0,
            live_resolver=lambda sessions: {"present": _live(pid=100)})   # 'omitted' absent
    out = d.tick(now=0.0)
    snap = read_status_snapshot(str(tmp_path))
    assert "skipped" not in out
    assert snap["seats"]["present"]["status"] == "idle"
    assert snap["seats"]["omitted"]["status"] == "stalled"    # frozen pid 300 + frozen hook


# --- E2: transcript turn-completion vetoes a stale-by-emission working hook ------

def test_slow_completed_turn_downgrades_stale_working_hook_to_idle(tmp_path):
    # missed-Stop residual: a seat's live hook froze at working (Claude emitted no
    # Stop on interrupt/kill), zero activity. The turn_resolver (transcript) says the
    # turn COMPLETED -> the daemon must read idle, NOT stalled.
    seat = _seat("frozen", 100)
    seat.hooks = {"state": "working"}                  # stale-by-emission working hook
    sampler = FakeSampler({100: IDLE_PROC})            # zero bytes / zero compute
    d = _mk(tmp_path, [seat], sampler, clock=lambda: 0.0,
            turn_resolver=lambda session, runtime, hooks: True)   # transcript: complete
    d.tick(now=0.0)
    assert read_status_snapshot(str(tmp_path))["seats"]["frozen"]["status"] == "idle"


def test_slow_open_turn_keeps_working_hook_stalled(tmp_path):
    # a GENUINE mid-tool hang: working hook + zero activity + transcript says the
    # turn is OPEN (pending tool_use) -> MUST stay stalled (never mask a real hang).
    seat = _seat("hung", 100)
    seat.hooks = {"state": "working"}
    sampler = FakeSampler({100: IDLE_PROC})
    d = _mk(tmp_path, [seat], sampler, clock=lambda: 0.0,
            turn_resolver=lambda session, runtime, hooks: False)  # transcript: open
    d.tick(now=0.0)
    assert read_status_snapshot(str(tmp_path))["seats"]["hung"]["status"] == "stalled"


def test_slow_no_turn_resolver_keeps_working_stalled_back_compat(tmp_path):
    # no transcript resolver wired -> trust the hook exactly as before (stalled).
    seat = _seat("legacy", 100)
    seat.hooks = {"state": "working"}
    sampler = FakeSampler({100: IDLE_PROC})
    d = _mk(tmp_path, [seat], sampler, clock=lambda: 0.0)   # no turn_resolver
    d.tick(now=0.0)
    assert read_status_snapshot(str(tmp_path))["seats"]["legacy"]["status"] == "stalled"


def test_slow_turn_resolver_only_called_for_working_hook_seats(tmp_path):
    # efficiency: the transcript tail-read must happen ONLY for working-hook seats
    # (the stalled candidates), never for idle/streaming seats.
    working = _seat("w", 100); working.hooks = {"state": "working"}
    idle = _seat("i", 200); idle.hooks = {"state": "idle"}
    sampler = FakeSampler({100: IDLE_PROC, 200: IDLE_PROC})
    called = []
    d = _mk(tmp_path, [working, idle], sampler, clock=lambda: 0.0,
            turn_resolver=lambda session, runtime, hooks: called.append(session) or True)
    d.tick(now=0.0)
    assert called == ["w"]                              # idle seat's transcript never read


def test_slow_turn_resolver_error_falls_back_to_hook_never_crashes(tmp_path):
    # a transcript read that raises must not crash the tick; fall back to trusting
    # the hook (stalled) — no worse than pre-E2.
    seat = _seat("boom", 100)
    seat.hooks = {"state": "working"}
    sampler = FakeSampler({100: IDLE_PROC})

    def boom(session, runtime, hooks):
        raise RuntimeError("transcript read exploded")

    d = _mk(tmp_path, [seat], sampler, clock=lambda: 0.0, turn_resolver=boom)
    out = d.tick(now=0.0)                               # must NOT raise
    assert "skipped" not in out
    assert read_status_snapshot(str(tmp_path))["seats"]["boom"]["status"] == "stalled"


def test_write_status_snapshot_io_error_is_contained(tmp_path, monkeypatch):
    import scripts.lineage_daemon.telemetryd as T  # noqa
    d = _mk(tmp_path, [_seat("gm", 100)], FakeSampler({100: IDLE_PROC}), clock=lambda: 0.0)
    monkeypatch.setattr("lineage_daemon.telemetryd.write_status_snapshot",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    out = d.tick(now=0.0)                                # must NOT raise
    assert isinstance(out, dict)


def test_slow_tick_picks_up_seats_spawned_after_boot(tmp_path):
    """A fresh install's first `orchestra spawn` happens AFTER telemetryd started: the
    registry-driven seats_refresh must add the seat on the next slow tick (and a
    refresh returning None keeps the current set)."""
    wal = tmp_path / "wal"; wal.mkdir()
    seats = [_seat("gm", 100, ring=FakeRing())]
    sampler = FakeSampler({100: IDLE_PROC, 200: IDLE_PROC})
    calls = {"n": 0}

    def refresh():
        calls["n"] += 1
        if calls["n"] == 1:
            return None                                  # registry unchanged
        return seats + [_seat("planner", 200, ring=FakeRing())]   # a new seat appeared

    d = TelemetryDaemon(
        seats=seats, mux=FakeMux(), sweep=FakeSweep(), sampler=sampler, flag_store=FakeFlags(),
        snapshot_base=str(tmp_path), wal_dir=str(wal), lock_path=str(tmp_path / "t.lock"),
        clock=lambda: 0.0, seats_refresh=refresh)
    d.tick(now=0.0)                                      # first slow tick: refresh says unchanged
    assert [s.session for s in d.seats] == ["gm"]
    d.tick(now=SLOW_POLL_S + 1.0)                        # next slow tick: the new seat appears
    assert [s.session for s in d.seats] == ["gm", "planner"]
    snap = read_status_snapshot(str(tmp_path))
    assert snap is not None and "planner" in snap["seats"]
