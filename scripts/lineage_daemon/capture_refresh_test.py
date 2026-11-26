"""telemetryd re-resolves each seat's capture path (gm msg_3b93dc9e, 2026-10-09).

telemetryd built its capture seats ONCE at boot and tailed each seat's FROZEN source_path, so every seat rotated
since the daemon started was captured only in rare one-off bursts (all 14 rotated seats on 10-09, up to 2.9 MB
behind). A rotation must move capture to the new transcript with no restart, and a seat spawned after boot must be
captured too.
"""
import json
import os

from lineage_daemon.wal.multiplexer import MultiplexedTailer, MuxSeat
from lineage_daemon.wal.store import WalStore

SID_A = "aaaaaaaa-1111-1111-1111-111111111111"
SID_B = "bbbbbbbb-2222-2222-2222-222222222222"


def _line(sid, text):
    return json.dumps({"type": "assistant", "sessionId": sid, "timestamp": "2026-10-09T10:00:00Z",
                       "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                                   "usage": {"input_tokens": 1, "output_tokens": 1}}}) + "\n"


def _events(wal, root):
    return [dict(e) for e in WalStore(os.path.join(wal, f"{root}.db")).events()]


def _has(events, text):
    return any(text in json.dumps(e, default=str) for e in events)


def _captured_at(events, path, offset):
    """The store keeps summaries + byte refs, not text: a line is captured iff an event's
    body_ref points at <path>:<offset of that line>."""
    return any(str(e.get("body_ref") or "").split("#")[0] == f"{path}:{offset}" for e in events)


# ---- MultiplexedTailer.refresh ------------------------------------------------------------------

def test_a_rotation_moves_capture_to_the_new_transcript(tmp_path):
    wal = str(tmp_path / "wal")
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text(_line(SID_A, "old gen"))
    mux = MultiplexedTailer(wal, wal)
    assert mux.register(MuxSeat("seat", "claude", str(a), SID_A, 1))
    mux.tick()
    b.write_text(_line(SID_B, "new gen says hi"))                  # the rotation: a new transcript
    out = mux.refresh([MuxSeat("seat", "claude", str(b), SID_B, 2)])
    assert out["rebuilt"] == ["seat"]
    mux.tick()
    gen2 = [e for e in _events(wal, "seat") if e["generation"] == 2]
    assert gen2 and all(e["source_path"] == str(b) for e in gen2)
    mux.close()


def test_after_a_rotation_the_old_transcript_is_no_longer_tailed(tmp_path):
    wal = str(tmp_path / "wal")
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text(_line(SID_A, "one"))
    b.write_text(_line(SID_B, "two"))
    mux = MultiplexedTailer(wal, wal)
    mux.register(MuxSeat("seat", "claude", str(a), SID_A, 1))
    mux.refresh([MuxSeat("seat", "claude", str(b), SID_B, 2)])
    late = a.stat().st_size
    with open(a, "a") as f:
        f.write(_line(SID_A, "a write after the rotation"))
    mux.tick()
    assert not _captured_at(_events(wal, "seat"), str(a), late)          # drained BEFORE, never after
    mux.close()


def test_a_seat_spawned_after_boot_is_registered(tmp_path):
    wal = str(tmp_path / "wal")
    b = tmp_path / "b.jsonl"
    b.write_text(_line(SID_B, "hello"))
    mux = MultiplexedTailer(wal, wal)
    out = mux.refresh([MuxSeat("newseat", "claude", str(b), SID_B, 1)])
    assert out["registered"] == ["newseat"]
    mux.tick()
    assert _events(wal, "newseat")
    mux.close()


def test_an_unchanged_seat_is_left_alone(tmp_path):
    wal = str(tmp_path / "wal")
    a = tmp_path / "a.jsonl"
    a.write_text(_line(SID_A, "x"))
    mux = MultiplexedTailer(wal, wal)
    seat = MuxSeat("seat", "claude", str(a), SID_A, 1)
    mux.register(seat)
    before = mux._seats["seat"]
    out = mux.refresh([MuxSeat("seat", "claude", str(a), SID_A, 1)])
    assert out["unchanged"] == 1 and not (out["rebuilt"] or out["registered"] or out["dropped"])
    assert mux._seats["seat"] is before
    mux.close()


def test_a_refresh_error_on_one_seat_never_stops_the_others(tmp_path, monkeypatch):
    wal = str(tmp_path / "wal")
    b = tmp_path / "b.jsonl"
    b.write_text(_line(SID_B, "x"))
    mux = MultiplexedTailer(wal, wal)
    bad = MuxSeat("bad", "claude", str(b), SID_B, None)                   # unusable seat
    out = mux.refresh([bad, MuxSeat("good", "claude", str(b), SID_B, 1)])
    assert "good" in out["registered"]
    mux.close()


# ---- the daemon calls the refresher on its own cadence ------------------------------------------

class _Mux:
    def __init__(self):
        self.refreshed = []
        self.degraded_lineages = {}

    def tick(self):
        return 0

    def refresh(self, seats, considered_roots=None):
        self.refreshed.append((list(seats), considered_roots))
        return {"rebuilt": [], "registered": [], "unchanged": len(seats)}


class _Flags:
    def blocked(self, *_a, **_k):
        return False


def _daemon(tmp_path, refresher, clock):
    from lineage_daemon.telemetryd import TelemetryDaemon

    class _Sweep:
        def sweep(self):
            pass

    class _Sampler:
        def sample(self, *_a, **_k):
            return {}

    return TelemetryDaemon(seats=[], mux=_Mux(), sweep=_Sweep(), sampler=_Sampler(), flag_store=_Flags(),
                           snapshot_base=str(tmp_path), wal_dir=str(tmp_path), clock=clock,
                           mux_refresher=refresher, mux_refresh_s=60.0)


def test_the_daemon_refreshes_capture_on_its_cadence(tmp_path):
    t = {"now": 1000.0}
    calls = []
    d = _daemon(tmp_path, lambda: calls.append(1) or (["S"], {"r"}), lambda: t["now"])
    d.tick(t["now"])                          # first slow tick: refresh
    for step in (5, 10, 30, 55):              # inside the 60 s cadence: slow ticks, no refresh
        t["now"] = 1000.0 + step
        d.tick(t["now"])
    assert len(calls) == 1 and d.mux.refreshed == [(["S"], {"r"})]
    t["now"] = 1061.0
    d.tick(t["now"])
    assert len(calls) == 2


def test_a_refresher_that_raises_never_breaks_the_tick(tmp_path):
    def boom():
        raise RuntimeError("stores unreadable")
    d = _daemon(tmp_path, boom, lambda: 1000.0)
    assert d.tick(1000.0)["slow"] is True


# ---- live wiring: build_live_daemon re-reads the stores -----------------------------------------

def test_build_live_daemon_refresher_follows_a_rotation(monkeypatch, tmp_path):
    from lineage_daemon.seat_builder import build_live_daemon, _Resolvers
    stores = {"agents": {"seat": {"session_id": SID_A, "runtime": "claude"}}}

    class _T:
        def list_sessions(self):
            return []

        def pipe_pane(self, *_a):
            pass

    r = _Resolvers(load_stores=lambda orch: ({}, stores["agents"]), live_sessions=lambda: {"seat"},
                   pane_pid=lambda s: None, live_resolver=lambda sessions: {s: {"pid": 4242, "hooks": None}
                                                                         for s in sessions},
                   source_path=lambda s, rec, pid: f"/x/{rec['session_id']}.jsonl",
                   hooks_for=lambda s, rt: None, ring_for=lambda s: None, tmux=_T(),
                   resolve_generation=lambda s, rec: 2 if rec["session_id"] == SID_B else 1)
    d = build_live_daemon(tmp_path, resolvers=r)
    stores["agents"]["seat"] = {"session_id": SID_B, "runtime": "claude"}       # rotation
    seats, considered = d._mux_refresher()
    assert [(m.lineage_root, m.source_path, m.generation) for m in seats] == [
        ("seat", f"/x/{SID_B}.jsonl", 2)]
    assert considered == {"seat"}
    d.mux.close()


# ---- gm msg_91f227e3 conditions --------------------------------------------------------------------

def test_the_old_transcript_is_drained_to_eof_before_switching(tmp_path):
    """A retiring generation writes its last lines after promotion: they must not be stranded."""
    wal = str(tmp_path / "wal")
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text(_line(SID_A, "early"))
    mux = MultiplexedTailer(wal, wal)
    mux.register(MuxSeat("seat", "claude", str(a), SID_A, 1))
    mux.tick()
    late = a.stat().st_size
    with open(a, "a") as f:
        f.write(_line(SID_A, "LAST WORDS of the old generation"))      # written after the last tick
    b.write_text(_line(SID_B, "new"))
    mux.refresh([MuxSeat("seat", "claude", str(b), SID_B, 2)], considered_roots={"seat"})
    gen1 = [e for e in _events(wal, "seat") if e["generation"] == 1]
    assert _captured_at(gen1, str(a), late)
    mux.close()


def test_a_retired_seat_is_drained_dropped_and_its_lock_released(tmp_path):
    from lineage_daemon.wal.wal_tailer import SeatLock
    wal = str(tmp_path / "wal")
    a = tmp_path / "a.jsonl"
    a.write_text(_line(SID_A, "x"))
    mux = MultiplexedTailer(wal, wal)
    mux.register(MuxSeat("gone", "claude", str(a), SID_A, 1))
    mux.tick()
    late = a.stat().st_size
    with open(a, "a") as f:
        f.write(_line(SID_A, "final line before retire"))
    held = mux._seats["gone"]          # keep it alive: GC must not be what releases the lock
    out = mux.refresh([], considered_roots=set())
    assert out["dropped"] == ["gone"] and "gone" not in mux._seats
    assert held.lock._fh is None         # released EXPLICITLY by the drop
    assert _captured_at(_events(wal, "gone"), str(a), late)
    lock = SeatLock("gone", wal)
    assert lock.acquire()                          # released: run_capture can own it again
    lock.release()
    mux.close()


def test_a_live_but_unresolvable_seat_keeps_its_capture(tmp_path):
    """Fail-closed: no fresh MuxSeat for a root the resolver DID consider -> keep, never drop."""
    wal = str(tmp_path / "wal")
    a = tmp_path / "a.jsonl"
    a.write_text(_line(SID_A, "x"))
    mux = MultiplexedTailer(wal, wal)
    mux.register(MuxSeat("seat", "claude", str(a), SID_A, 1))
    out = mux.refresh([], considered_roots={"seat"})
    assert out["kept_unresolved"] == ["seat"] and "seat" in mux._seats
    mux.close()


def test_without_considered_roots_nothing_is_dropped(tmp_path):
    wal = str(tmp_path / "wal")
    a = tmp_path / "a.jsonl"
    a.write_text(_line(SID_A, "x"))
    mux = MultiplexedTailer(wal, wal)
    mux.register(MuxSeat("seat", "claude", str(a), SID_A, 1))
    assert mux.refresh([])["dropped"] == [] and "seat" in mux._seats
    mux.close()


def test_one_log_line_per_change(tmp_path, caplog):
    import logging
    wal = str(tmp_path / "wal")
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text(_line(SID_A, "x"))
    b.write_text(_line(SID_B, "y"))
    mux = MultiplexedTailer(wal, wal)
    mux.register(MuxSeat("seat", "claude", str(a), SID_A, 1))
    with caplog.at_level(logging.WARNING):
        mux.refresh([MuxSeat("seat", "claude", str(b), SID_B, 2),
                     MuxSeat("fresh", "claude", str(b), SID_B, 1)], considered_roots={"seat", "fresh"})
    msgs = [r.getMessage() for r in caplog.records]
    assert [m for m in msgs if "REPATH seat" in m and SID_A[:8] in m and SID_B[:8] in m and "gen 1 -> 2" in m]
    assert [m for m in msgs if "REGISTER fresh" in m]
    mux.close()


# ---- linger (gm msg_17d5a7bd) -------------------------------------------------------------------------

def test_a_blue_still_finishing_after_the_repath_is_captured_on_the_next_refresh(tmp_path):
    wal = str(tmp_path / "wal")
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text(_line(SID_A, "x"))
    b.write_text(_line(SID_B, "y"))
    mux = MultiplexedTailer(wal, wal)
    mux.register(MuxSeat("seat", "claude", str(a), SID_A, 1))
    new = MuxSeat("seat", "claude", str(b), SID_B, 2)
    mux.refresh([new], considered_roots={"seat"})                       # the repath tick
    late = a.stat().st_size
    with open(a, "a") as f:
        f.write(_line(SID_A, "the blue's very last turn, AFTER the repath tick"))
    out = mux.refresh([new], considered_roots={"seat"})                 # the next refresh
    assert out["lingered"] == ["seat"]
    gen1 = [e for e in _events(wal, "seat") if e["generation"] == 1]
    assert _captured_at(gen1, str(a), late)
    mux.close()


def test_the_linger_ends_after_two_quiet_refreshes(tmp_path):
    wal = str(tmp_path / "wal")
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text(_line(SID_A, "x"))
    b.write_text(_line(SID_B, "y"))
    mux = MultiplexedTailer(wal, wal)
    mux.register(MuxSeat("seat", "claude", str(a), SID_A, 1))
    new = MuxSeat("seat", "claude", str(b), SID_B, 2)
    mux.refresh([new], considered_roots={"seat"})
    assert mux.refresh([new], considered_roots={"seat"})["linger_done"] == []
    assert mux.refresh([new], considered_roots={"seat"})["linger_done"] == ["seat"]
    assert "seat" not in mux._lingering
    mux.close()


def test_the_linger_is_capped_in_time(tmp_path, monkeypatch):
    from lineage_daemon.wal import multiplexer as M
    wal = str(tmp_path / "wal")
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text(_line(SID_A, "x"))
    b.write_text(_line(SID_B, "y"))
    mux = MultiplexedTailer(wal, wal)
    mux.register(MuxSeat("seat", "claude", str(a), SID_A, 1))
    new = MuxSeat("seat", "claude", str(b), SID_B, 2)
    mux.refresh([new], considered_roots={"seat"})
    mux._lingering["seat"][3] -= M.LINGER_MAX_S + 1                    # started long ago
    with open(a, "a") as f:
        f.write(_line(SID_A, "still growing"))                          # growth alone can't keep it forever
    assert mux.refresh([new], considered_roots={"seat"})["linger_done"] == ["seat"]
    mux.close()


# ---- review of #610 (Claude reviewer): a swap's green never takes its blue's capture early ----------

def test_an_unpromoted_green_does_not_take_the_capture_from_its_working_blue(tmp_path):
    """Blue (canonical) and its pre-allocated green are both live on one root during a swap. The green's
    transcript exists before promotion; capture must stay on the blue, whose lines would otherwise be
    captured only while it lingers (two quiet refreshes, or 15 min at most)."""
    wal = str(tmp_path / "wal")
    blue, green = tmp_path / "blue.jsonl", tmp_path / "green.jsonl"
    blue.write_text(_line(SID_A, "blue working"))
    green.write_text(_line(SID_B, "green hydrated"))
    mux = MultiplexedTailer(wal, wal)
    b = MuxSeat("gm", "claude", str(blue), SID_A, 7, canonical=True)
    g = MuxSeat("gm", "claude", str(green), SID_B, 8, canonical=False)
    assert mux.register(b)
    for order in ([b, g], [g, b]):                       # registry order must not matter
        out = mux.refresh(order, considered_roots={"gm"})
        assert out["rebuilt"] == [] and mux._seats["gm"].seat.sid == SID_A, out
    # promotion moves the pointer: now the green wins
    out = mux.refresh([MuxSeat("gm", "claude", str(blue), SID_A, 7, canonical=False),
                       MuxSeat("gm", "claude", str(green), SID_B, 8, canonical=True)], considered_roots={"gm"})
    assert out["rebuilt"] == ["gm"] and mux._seats["gm"].seat.sid == SID_B
    mux.close()


def test_without_a_canonical_answer_the_seat_already_captured_stays(tmp_path):
    wal = str(tmp_path / "wal")
    blue, green = tmp_path / "blue.jsonl", tmp_path / "green.jsonl"
    blue.write_text(_line(SID_A, "blue"))
    green.write_text(_line(SID_B, "green"))
    mux = MultiplexedTailer(wal, wal)
    assert mux.register(MuxSeat("gm", "claude", str(blue), SID_A, 7))
    out = mux.refresh([MuxSeat("gm", "claude", str(blue), SID_A, 7), MuxSeat("gm", "claude", str(green), SID_B, 8)])
    assert out["rebuilt"] == [] and mux._seats["gm"].seat.sid == SID_A
    mux.close()


def _live_daemon(tmp_path, agents, live, canonical=None):
    from lineage_daemon.seat_builder import build_live_daemon, _Resolvers

    class _T:
        def list_sessions(self):
            return []

        def pipe_pane(self, *_a):
            pass
    r = _Resolvers(load_stores=lambda orch: ({}, agents), live_sessions=lambda: set(live["now"]),
                   pane_pid=lambda s: None, live_resolver=lambda sessions: {s: {"pid": 4242, "hooks": None}
                                                                         for s in sessions},
                   source_path=lambda s, rec, pid: f"/x/{rec['session_id']}.jsonl",
                   hooks_for=lambda s, rt: None, ring_for=lambda s: None, tmux=_T(),
                   resolve_generation=lambda s, rec: rec.get("generation", 1),
                   is_canonical=canonical)
    return build_live_daemon(tmp_path, resolvers=r)


def test_the_refresher_marks_which_seat_is_canonical(tmp_path):
    agents = {"gm": {"session_id": SID_A, "runtime": "claude", "generation": 7},
              "gm-g8": {"session_id": SID_B, "runtime": "claude", "generation": 8, "lineage_root": "gm"}}
    d = _live_daemon(tmp_path, agents, {"now": {"gm", "gm-g8"}},
                     canonical=lambda root, rec: rec["session_id"] == SID_A)
    seats, _ = d._mux_refresher()
    assert {(m.sid, m.canonical) for m in seats} == {(SID_A, True), (SID_B, False)}
    d.mux.close()


def test_an_empty_tmux_listing_drops_no_capture(tmp_path):
    """get_live_tmux_sessions answers set() on any tmux error: that must not read as every seat gone."""
    agents = {"seat": {"session_id": SID_A, "runtime": "claude"}}
    live = {"now": {"seat"}}
    d = _live_daemon(tmp_path, agents, live)
    _, considered = d._mux_refresher()
    assert considered == {"seat"}
    live["now"] = set()
    _, considered = d._mux_refresher()
    assert considered is None
    d.mux.close()
