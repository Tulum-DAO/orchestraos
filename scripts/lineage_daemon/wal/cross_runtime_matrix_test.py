"""Cross-runtime matrix leg (Build A acceptance GATE).

The commission's hard gate: acceptance MUST prove out on >=1 claude + >=1 codex
OR gemini seat in the SAME process. A claude-only proof is NOT acceptable (same
discipline as crossmodel-verify). This drives all three provider adapters
through the ONE multiplexer process and asserts every runtime is captured with a
coherent per-lineage db.

Sources are hermetic synthetic fixtures (no real model voice). The live-gemini
by-effect proof (815 real steps polled RO from a live antigravity db) is recorded
separately in the gm evidence handoff.
"""
import os
import sqlite3
import tempfile

from lineage_daemon.wal.multiplexer import MultiplexedTailer, MuxSeat


C_SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
X_SID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

_CLAUDE = ('{"type":"assistant","sessionId":"%s","timestamp":'
           '"2026-09-03T00:00:00Z","message":{"role":"assistant","content":'
           '[{"type":"tool_use","name":"Bash"},{"type":"text","text":"SYN ok"}],'
           '"usage":{"input_tokens":5,"output_tokens":1}}}\n') % C_SID

_CODEX = "\n".join([
    '{"type":"session_meta","payload":{"id":"%s","timestamp":"2026-09-03T00:00:00Z","cwd":"/tmp"}}' % X_SID,
    '{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{}","call_id":"c1"}}',
    '{"type":"response_item","payload":{"type":"function_call_output","call_id":"c1","output":"done"}}',
    "",
])


def _gemini_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE steps (idx integer PRIMARY KEY, step_type integer"
                 " NOT NULL DEFAULT 0, status integer NOT NULL DEFAULT 0,"
                 " permissions blob, step_payload blob)")
    conn.executemany("INSERT INTO steps (idx, step_type, status, step_payload)"
                     " VALUES (?,?,?,?)",
                     [(i, 15, 3, bytes.fromhex("080f20032a02")) for i in (1, 2, 3)])
    conn.commit(); conn.close()


def test_matrix_claude_codex_gemini_one_process():
    with tempfile.TemporaryDirectory() as tmp:
        wal_dir = os.path.join(tmp, "wal")
        cj = os.path.join(tmp, "c.jsonl"); open(cj, "w").write(_CLAUDE)
        xj = os.path.join(tmp, "x.jsonl"); open(xj, "w").write(_CODEX)
        gdb = os.path.join(tmp, "g.db"); _gemini_db(gdb)

        mux = MultiplexedTailer(wal_dir, os.path.join(tmp, "locks"))
        assert mux.register(MuxSeat("c-seat", "claude", cj, C_SID, 1))
        assert mux.register(MuxSeat("x-seat", "codex", xj, X_SID, 1))
        assert mux.register(MuxSeat("g-seat", "gemini", gdb, "g", 1))

        assert mux.tick() > 0
        runtimes = mux.captured_runtimes()
        # THE GATE: all three runtimes captured by one process; and at minimum a
        # non-claude runtime is present (claude-only is unacceptable).
        assert runtimes == {"claude", "codex", "gemini"}
        assert runtimes - {"claude"}, "matrix requires a non-claude runtime"
        mux.close()


def test_each_runtime_lands_in_its_own_lineage_db():
    with tempfile.TemporaryDirectory() as tmp:
        wal_dir = os.path.join(tmp, "wal")
        cj = os.path.join(tmp, "c.jsonl"); open(cj, "w").write(_CLAUDE)
        gdb = os.path.join(tmp, "g.db"); _gemini_db(gdb)
        mux = MultiplexedTailer(wal_dir, os.path.join(tmp, "locks"))
        mux.register(MuxSeat("c-seat", "claude", cj, C_SID, 1))
        mux.register(MuxSeat("g-seat", "gemini", gdb, "g", 1))
        mux.tick()
        # per-lineage isolation (rotation-survival semantics)
        cstore = sqlite3.connect(os.path.join(wal_dir, "c-seat.db"))
        gstore = sqlite3.connect(os.path.join(wal_dir, "g-seat.db"))
        try:
            assert cstore.execute(
                "SELECT DISTINCT runtime FROM wal_events").fetchall() == [("claude",)]
            assert gstore.execute(
                "SELECT DISTINCT runtime FROM wal_events").fetchall() == [("gemini",)]
        finally:
            cstore.close(); gstore.close()
        mux.close()
