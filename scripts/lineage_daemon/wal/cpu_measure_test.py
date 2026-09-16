"""CPU budget — <1% MEASURED, not asserted (Build A hard constraint).

Methodology (mirrors the A0 CPU measure, honest): measure the CPU TIME the
multiplexer spends doing M capture ticks over a realistic quiescent fleet, then
compute the STEADY-STATE percentage as cpu_time / (M * poll_interval) — because
in production M ticks span M*interval wall-seconds (dominated by the >=5s sleep)
and CPU is consumed only during the tick work. os.times() is read INCLUDING
children (children_user/children_system) so any forked git subprocess is counted.

HARD carry-in from A0: never scan /proc per-agent (ps-fork-per-agent = 2.57%
FAIL). Build A's capture lane is file-tail + one sqlite RO poll per gemini seat +
a cheap sample — it touches /proc NOT AT ALL. `test_no_per_agent_proc_scan`
guards that structurally.
"""
import os
import sqlite3
import tempfile

from lineage_daemon.wal.multiplexer import MultiplexedTailer, MuxSeat, MIN_POLL_S


def _claude_src(path, sid):
    open(path, "w").write(
        '{"type":"assistant","sessionId":"%s","timestamp":"2026-09-03T00:00:00Z",'
        '"message":{"role":"assistant","content":[{"type":"text","text":"x"}],'
        '"usage":{"input_tokens":1,"output_tokens":1}}}\n' % sid)


def _codex_src(path, sid):
    open(path, "w").write("\n".join([
        '{"type":"session_meta","payload":{"id":"%s","timestamp":"2026-09-03T00:00:00Z","cwd":"/tmp"}}' % sid,
        '{"type":"event_msg","payload":{"type":"token_count","info":{"total_tokens":1}}}',
        "",
    ]))


def _gemini_src(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE steps (idx integer PRIMARY KEY, step_type integer"
                 " NOT NULL DEFAULT 0, status integer NOT NULL DEFAULT 0,"
                 " permissions blob, step_payload blob)")
    conn.executemany("INSERT INTO steps (idx, step_type, status, step_payload)"
                     " VALUES (?,?,?,?)",
                     [(i, 15, 3, bytes.fromhex("080f20032a02")) for i in range(1, 6)])
    conn.commit(); conn.close()


def _cpu_seconds(t0, t1):
    return ((t1.user - t0.user) + (t1.system - t0.system)
            + (t1.children_user - t0.children_user)
            + (t1.children_system - t0.children_system))


def _build_fleet(tmp, n_claude, n_codex, n_gemini):
    wal_dir = os.path.join(tmp, "wal")
    mux = MultiplexedTailer(wal_dir, os.path.join(tmp, "locks"))
    for i in range(n_claude):
        p = os.path.join(tmp, f"c{i}.jsonl")
        _claude_src(p, f"c-{i:08d}-0000-0000-0000-000000000000")
        mux.register(MuxSeat(f"c{i}", "claude", p, f"c-{i}", 1))
    for i in range(n_codex):
        p = os.path.join(tmp, f"x{i}.jsonl")
        _codex_src(p, f"x-{i:08d}-0000-0000-0000-000000000000")
        mux.register(MuxSeat(f"x{i}", "codex", p, f"x-{i}", 1))
    for i in range(n_gemini):
        p = os.path.join(tmp, f"g{i}.db")
        _gemini_src(p)
        mux.register(MuxSeat(f"g{i}", "gemini", p, f"g{i}", 1))
    return mux


def test_steady_state_cpu_under_one_percent():
    # a realistic fleet skew: mostly claude, a few codex, several gemini (the
    # heaviest — a sqlite RO connect+poll per tick).
    n_claude, n_codex, n_gemini = 30, 4, 8
    ticks = 300
    with tempfile.TemporaryDirectory() as tmp:
        mux = _build_fleet(tmp, n_claude, n_codex, n_gemini)
        try:
            mux.tick()  # warm-up: first tick captures the initial events
            # steady state: all seats quiescent, each tick is the real idle cost
            t0 = os.times()
            for _ in range(ticks):
                appended = mux.tick()
                assert appended == 0  # quiescent — no re-read
            t1 = os.times()
        finally:
            mux.close()
        cpu = _cpu_seconds(t0, t1)
        seats = n_claude + n_codex + n_gemini
        steady_state_pct = cpu / (ticks * MIN_POLL_S) * 100.0
        print(f"\n[cpu-measure] seats={seats} ticks={ticks} interval={MIN_POLL_S}s "
              f"cpu={cpu:.4f}s steady_state={steady_state_pct:.4f}% core")
        assert steady_state_pct < 1.0, (
            f"steady-state CPU {steady_state_pct:.4f}% exceeds the <1% budget")


def test_no_per_agent_proc_scan():
    """Structural guard: the Build A capture lane must NEVER scan /proc (A0's
    ps-fork-per-agent FAIL). Checks for actual /proc filesystem ACCESS idioms
    (not the word /proc in the docstrings that explain WHY we avoid it)."""
    here = os.path.dirname(__file__)
    # real access patterns a /proc scanner would use
    access_idioms = ("'/proc", '"/proc', "listdir('/proc", 'listdir("/proc',
                     "join('/proc", 'join("/proc', "/proc/")
    for mod in ("multiplexer.py", "adapter_codex.py", "adapter_gemini.py",
                "normalize.py"):
        with open(os.path.join(here, mod)) as fh:
            code_lines = []
            for line in fh:
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                code_lines.append(line)
            src = "".join(code_lines)
        for idiom in access_idioms:
            assert idiom not in src, f"{mod} uses a /proc access idiom: {idiom!r}"

