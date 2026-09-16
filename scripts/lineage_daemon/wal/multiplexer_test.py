"""RED-first tests for the multiplexed WAL tailer daemon (Build A core).

ONE process, N cursors, one liveness-lock — the fleet-wide normalized capture
lane that EXTENDS the stage-1/2 store/adapter/enricher lane. NOT a net-new
daemon codebase; the multiplexer is the per-seat cursor manager + single-process
supervisor over the existing per-provider adapters.

Invariants under test:
  * one daemon tails N seats via N cursors (not N processes);
  * provider-agnostic dispatch: claude jsonl + codex rollout + gemini sqlite-WAL
    in the SAME process;
  * ONE liveness-lock, pid-liveness-checked (survives SIGKILL: a stale pid is
    taken over; a LIVE holder is refused);
  * per-seat SeatLock COEXISTENCE: a seat already owned by a run_capture tailer
    is SKIPPED, never double-written;
  * honors the BG_DISABLED fleet e-brake (rider ii);
  * INERT — importing/constructing writes nothing; only tick() captures.
"""
import os
import sqlite3
import tempfile

from lineage_daemon.wal.multiplexer import (
    MultiplexedTailer, MuxSeat, LivenessLock, make_adapter,
)
from lineage_daemon.wal.adapter_claude import ClaudeWalAdapter
from lineage_daemon.wal.adapter_codex import CodexWalAdapter
from lineage_daemon.wal.adapter_gemini import GeminiWalAdapter
from lineage_daemon.wal.wal_tailer import SeatLock
from lineage_daemon.wal.store import WalStore


CLAUDE_SID = "11111111-1111-1111-1111-111111111111"
CODEX_SID = "22222222-2222-2222-2222-222222222222"

_CLAUDE_LINE = ('{"type":"assistant","sessionId":"%s","timestamp":'
                '"2026-09-03T00:00:00Z","message":{"role":"assistant",'
                '"content":[{"type":"text","text":"SYN hi"}],'
                '"usage":{"input_tokens":10,"output_tokens":2}}}\n') % CLAUDE_SID

_CODEX_LINES = "\n".join([
    '{"type":"session_meta","payload":{"id":"%s","timestamp":"2026-09-03T00:00:00Z","cwd":"/tmp"}}' % CODEX_SID,
    '{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"go"}]}}',
    '{"type":"event_msg","payload":{"type":"token_count","info":{"total_tokens":42}}}',
    "",
])


def _make_gemini_db(path, n=2):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE steps (idx integer PRIMARY KEY, step_type integer"
                 " NOT NULL DEFAULT 0, status integer NOT NULL DEFAULT 0,"
                 " permissions blob, step_payload blob)")
    conn.executemany("INSERT INTO steps (idx, step_type, status, step_payload)"
                     " VALUES (?,?,?,?)",
                     [(i, 15, 3, bytes.fromhex("080f20032a02")) for i in range(1, n + 1)])
    conn.commit(); conn.close()


def _claude_seat(tmp):
    src = os.path.join(tmp, "claude.jsonl")
    with open(src, "w") as fh:
        fh.write(_CLAUDE_LINE)
    return MuxSeat(lineage_root="claude-seat", runtime="claude",
                   source_path=src, sid=CLAUDE_SID, generation=1)


def _codex_seat(tmp):
    src = os.path.join(tmp, "codex.jsonl")
    with open(src, "w") as fh:
        fh.write(_CODEX_LINES)
    return MuxSeat(lineage_root="codex-seat", runtime="codex",
                   source_path=src, sid=CODEX_SID, generation=1)


def _gemini_seat(tmp):
    db = os.path.join(tmp, "gem.db")
    _make_gemini_db(db)
    return MuxSeat(lineage_root="gem-seat", runtime="gemini",
                   source_path=db, sid="gem", generation=1)


# --- adapter dispatch -------------------------------------------------------

def test_make_adapter_dispatches_by_runtime():
    with tempfile.TemporaryDirectory() as tmp:
        st = WalStore(os.path.join(tmp, "x.db"))
        assert isinstance(make_adapter("claude", st, "r", 0), ClaudeWalAdapter)
        assert isinstance(make_adapter("codex", st, "r", 0), CodexWalAdapter)
        assert isinstance(make_adapter("gemini", st, "r", 0), GeminiWalAdapter)
        # unknown runtime fails SAFE to claude (runtime_signatures precedent)
        assert isinstance(make_adapter("wat", st, "r", 0), ClaudeWalAdapter)


# --- multiplex: one process, N cursors --------------------------------------

def test_one_process_tails_n_seats():
    with tempfile.TemporaryDirectory() as tmp:
        wal_dir = os.path.join(tmp, "wal")
        lock_dir = os.path.join(tmp, "locks")
        mux = MultiplexedTailer(wal_dir, lock_dir)
        assert mux.register(_claude_seat(tmp)) is True
        assert mux.register(_codex_seat(tmp)) is True
        assert mux.register(_gemini_seat(tmp)) is True
        n = mux.tick()
        assert n > 0
        # each seat captured into its OWN per-lineage db (N cursors, N stores)
        assert os.path.exists(os.path.join(wal_dir, "claude-seat.db"))
        assert os.path.exists(os.path.join(wal_dir, "codex-seat.db"))
        assert os.path.exists(os.path.join(wal_dir, "gem-seat.db"))
        runtimes = mux.captured_runtimes()
        assert {"claude", "codex", "gemini"} <= runtimes
        mux.close()


def test_tick_is_incremental_per_seat_cursor():
    with tempfile.TemporaryDirectory() as tmp:
        mux = MultiplexedTailer(os.path.join(tmp, "wal"), os.path.join(tmp, "locks"))
        mux.register(_claude_seat(tmp))
        mux.register(_codex_seat(tmp))
        first = mux.tick()
        assert first > 0
        # nothing new -> zero (each seat advanced its own cursor)
        assert mux.tick() == 0
        mux.close()


# --- per-seat SeatLock coexistence ------------------------------------------

def test_seat_owned_by_run_capture_is_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        wal_dir = os.path.join(tmp, "wal")
        os.makedirs(wal_dir, exist_ok=True)
        # simulate a run_capture tailer already holding the per-seat lock
        held = SeatLock("claude-seat", wal_dir)
        assert held.acquire() is True
        try:
            mux = MultiplexedTailer(wal_dir, wal_dir)
            # the mux MUST NOT double-write a seat another tailer owns
            assert mux.register(_claude_seat(tmp)) is False
            # a free seat still registers
            assert mux.register(_codex_seat(tmp)) is True
            mux.close()
        finally:
            held.release()


# --- one liveness-lock, pid-liveness-checked --------------------------------

def test_liveness_lock_single_instance_and_reacquire():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "mux.lock")
        a = LivenessLock(path)
        assert a.acquire() is True
        # a second instance is refused while A holds it (single-instance)
        b = LivenessLock(path)
        assert b.acquire() is False
        # once A releases, the lock is free again (no stale wedge)
        a.release()
        c = LivenessLock(path)
        assert c.acquire() is True
        c.release()


def test_liveness_lock_survives_sigkill_of_holder():
    """A SIGKILLed holder never wedges the lock — the kernel releases the flock
    on death, so a fresh acquire succeeds (the commission's core requirement)."""
    import signal
    import subprocess
    import sys
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "mux.lock")
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                    "..", "..", ".."))
        code = (
            "import sys,time,fcntl,os\n"
            "sys.path.insert(0, %r)\n"
            "from scripts.lineage_daemon.wal.multiplexer import LivenessLock\n"
            "l=LivenessLock(%r)\n"
            "assert l.acquire() is True\n"
            "print('LOCKED', flush=True)\n"
            "time.sleep(30)\n" % (scripts_dir, path)
        )
        p = subprocess.Popen([sys.executable, "-c", code],
                             stdout=subprocess.PIPE, text=True)
        try:
            assert p.stdout.readline().strip() == "LOCKED"
            # holder alive -> we are refused
            assert LivenessLock(path).acquire() is False
        finally:
            p.send_signal(signal.SIGKILL)
            p.wait(timeout=5)
        # holder is dead -> the lock is free, no stale wedge
        winner = LivenessLock(path)
        assert winner.acquire() is True
        winner.release()


def test_run_refuses_when_lock_held():
    with tempfile.TemporaryDirectory() as tmp:
        import fcntl
        lock_path = os.path.join(tmp, "mux.lock")
        # a separate open-file-description holds the flock (models another daemon)
        holder_fd = open(lock_path, "w")
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            mux = MultiplexedTailer(os.path.join(tmp, "wal"),
                                    os.path.join(tmp, "locks"),
                                    lock_path=lock_path)
            reason = mux.run(interval=5.0, max_iters=1)
            assert reason == "lock-held"
            mux.close()
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            holder_fd.close()


# --- TELEMETRY_DISABLED e-brake (DEC-1788479670: the tailer is a pure observer,
# telemetry-owned — it honors the telemetry kill, NOT the arm kill BG_DISABLED,
# which would leave the durable rotation-survival store permanently dark while the
# arm is un-tapped) -----------------------------------------------------------

def test_telemetry_disabled_pauses_capture():
    with tempfile.TemporaryDirectory() as tmp:
        wal_dir = os.path.join(tmp, "wal")
        os.makedirs(wal_dir, exist_ok=True)
        mux = MultiplexedTailer(wal_dir, os.path.join(tmp, "locks"))
        mux.register(_claude_seat(tmp))
        open(os.path.join(wal_dir, "TELEMETRY_DISABLED"), "w").close()
        assert mux.tick() == 0  # paused, nothing captured
        os.remove(os.path.join(wal_dir, "TELEMETRY_DISABLED"))
        assert mux.tick() > 0   # resumes
        mux.close()


def test_bg_disabled_does_NOT_pause_the_durable_tailer():
    # the durable rotation-survival lane MUST keep capturing under BG_DISABLED
    # (present-by-design while the arm is un-tapped) — it is a pure observer and
    # manual/supervised rotations happening now still need survival capture.
    with tempfile.TemporaryDirectory() as tmp:
        wal_dir = os.path.join(tmp, "wal")
        os.makedirs(wal_dir, exist_ok=True)
        mux = MultiplexedTailer(wal_dir, os.path.join(tmp, "locks"))
        mux.register(_claude_seat(tmp))
        open(os.path.join(wal_dir, "BG_DISABLED"), "w").close()   # arm off
        assert mux.tick() > 0   # STILL captures (no longer dark)
        mux.close()


# --- inertness --------------------------------------------------------------

def test_construction_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        wal_dir = os.path.join(tmp, "wal")
        MultiplexedTailer(wal_dir, os.path.join(tmp, "locks"))
        # constructing the supervisor captures nothing and creates no lineage dbs
        assert not (os.path.isdir(wal_dir) and
                    [f for f in os.listdir(wal_dir) if f.endswith(".db")]
                    ) if os.path.isdir(wal_dir) else True


def test_min_poll_floor_enforced():
    import pytest
    with tempfile.TemporaryDirectory() as tmp:
        mux = MultiplexedTailer(os.path.join(tmp, "wal"), os.path.join(tmp, "locks"),
                                lock_path=os.path.join(tmp, "mux.lock"))
        with pytest.raises(ValueError):
            mux.run(interval=1.0, max_iters=1)
        mux.close()


def test_one_bad_seat_does_not_kill_the_tick():
    # DEC-1788481319: a fleet observer must NEVER die of one seat's bad data.
    with tempfile.TemporaryDirectory() as tmp:
        wal_dir = os.path.join(tmp, "wal")
        os.makedirs(wal_dir, exist_ok=True)
        mux = MultiplexedTailer(wal_dir, os.path.join(tmp, "locks"))
        mux.register(_claude_seat(tmp))                 # a healthy seat

        class _Boom:
            def tick(self):
                raise RuntimeError("poison seat")
            def close(self):
                pass
        mux._seats["boom-root"] = _Boom()               # inject a raising seat

        n = mux.tick()                                  # must NOT raise
        assert isinstance(n, int)
        assert "boom-root" in mux.degraded_lineages     # bad seat surfaced degraded
        mux.close()
