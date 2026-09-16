"""RED-first tests for Build B's realtime snapshot sink (the language-neutral
seam the LIVE B1 daemon writes and the TS telemetry API reads).

INERT: this is an ADDITIVE writer/reader contract. It does NOT modify B1's
merged daemon; wiring daemon.tick() -> write_status_snapshot is the LAST
(systemd) step. The API reads whatever is there (absent => graceful empty).

Location-agnostic: base dir resolves from ORCHESTRA_REALTIME_DIR or the
~/.orchestra/realtime default, so a location ruling flips one default, not
these tests.
"""
import json
import os
import tempfile

from lineage_daemon.realtime import snapshot


def test_status_schema_and_atomic_write_read_roundtrip():
    with tempfile.TemporaryDirectory() as base:
        snapshot.write_status_snapshot(base, {
            "gm": {"lineage_root": "gm-root", "runtime": "claude", "status": "idle"},
            "codex-dev-1": {"lineage_root": "cx-root", "runtime": "codex",
                            "status": "computing"},
        })
        snap = snapshot.read_status_snapshot(base)
        assert snap["schema"] == snapshot.STATUS_SCHEMA
        assert isinstance(snap["ts"], (int, float))
        assert set(snap["seats"]) == {"gm", "codex-dev-1"}
        gm = snap["seats"]["gm"]
        assert gm["session"] == "gm"                     # session backfilled from key
        assert gm["lineage_root"] == "gm-root"
        assert gm["runtime"] == "claude"
        assert gm["status"] == "idle"
        assert isinstance(gm["ts"], (int, float))


def test_read_absent_snapshot_returns_none():
    with tempfile.TemporaryDirectory() as base:
        assert snapshot.read_status_snapshot(base) is None   # INERT daemon unwired


def test_write_is_atomic_no_partial_file_on_reader():
    # tmp+rename: a reader never sees a half-written status.json.
    with tempfile.TemporaryDirectory() as base:
        snapshot.write_status_snapshot(base, {"a": {"lineage_root": "r",
                                       "runtime": "claude", "status": "idle"}})
        # no *.tmp left behind
        leftovers = [f for f in os.listdir(snapshot.realtime_dir(base))
                     if f.endswith(".tmp")]
        assert leftovers == []


def test_delta_log_append_and_tail_clean_frames():
    with tempfile.TemporaryDirectory() as base:
        snapshot.append_delta(base, "gm", "hello", seq=1)
        snapshot.append_delta(base, "gm", "world", seq=2)
        frames = snapshot.read_delta_frames(base, "gm", after_seq=0)
        assert [f["seq"] for f in frames] == [1, 2]
        assert [f["text"] for f in frames] == ["hello", "world"]
        # after_seq filters to only the tail
        assert [f["seq"] for f in snapshot.read_delta_frames(base, "gm", after_seq=1)] == [2]


def test_delta_log_absent_session_returns_empty():
    with tempfile.TemporaryDirectory() as base:
        assert snapshot.read_delta_frames(base, "nope", after_seq=0) == []


# --- BLOCKING-2 (claude COUNTER): the DAEMON WRITE-GATE is load-bearing -------
# The mid-stream clean->flagged guarantee lives HERE: a flagged/fail-closed
# lineage MUST append ZERO body bytes to the delta log. The API slow re-check is
# only the backstop. This is the primary safety mechanism, RED'd directly.

def _flag_store(tmp, flagged):
    from lineage_daemon.realtime.lineage_flag import LineageFlagStore
    p = os.path.join(tmp, "lineage_flags.json")
    json.dump({"schema": "lineage-flags/v1",
               "flagged": {r: {"reason": "court"} for r in flagged}}, open(p, "w"))
    return LineageFlagStore(p)


def test_write_gate_flagged_lineage_appends_zero_bytes():
    with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as ft:
        store = _flag_store(ft, flagged=["dirty-root"])
        wrote = snapshot.append_delta_if_clean(
            base, "codex-dev-1", "codex", b"SECRET-COURT-BYTES court court",
            lineage_root="dirty-root", flag_store=store, seq=1)
        assert wrote is False                                   # nothing appended
        # the on-disk log has ZERO bytes of the flagged body
        frames = snapshot.read_delta_frames(base, "codex-dev-1", after_seq=0)
        assert frames == []
        log = os.path.join(snapshot.realtime_dir(base), "deltas", "codex-dev-1.log")
        blob = open(log).read() if os.path.exists(log) else ""
        assert "SECRET-COURT-BYTES" not in blob


def test_write_gate_fail_closed_on_unreadable_flag_store_appends_zero():
    from lineage_daemon.realtime.lineage_flag import LineageFlagStore
    with tempfile.TemporaryDirectory() as base:
        store = LineageFlagStore("/nope/flags.json")            # fail-closed
        wrote = snapshot.append_delta_if_clean(
            base, "gm", "claude", b"anything at all",
            lineage_root="whatever", flag_store=store, seq=1)
        assert wrote is False
        assert snapshot.read_delta_frames(base, "gm", after_seq=0) == []


def test_write_gate_empty_lineage_root_appends_zero():
    with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as ft:
        store = _flag_store(ft, flagged=[])
        wrote = snapshot.append_delta_if_clean(
            base, "gm", "claude", b"body", lineage_root="",     # BLOCKING-1
            flag_store=store, seq=1)
        assert wrote is False


def test_write_gate_clean_lineage_appends_ansi_stripped_text():
    with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as ft:
        store = _flag_store(ft, flagged=["dirty"])
        wrote = snapshot.append_delta_if_clean(
            base, "gm", "claude", "\x1b[1mhello\x1b[0m world",
            lineage_root="clean-root", flag_store=store, seq=1)
        assert wrote is True
        frames = snapshot.read_delta_frames(base, "gm", after_seq=0)
        assert [f["text"] for f in frames] == ["hello world"]   # ANSI-stripped


# --- should-conditions: body-free snapshot, path sanitize, bounded log --------

def test_status_snapshot_is_body_free():
    # status.json must carry status/metadata ONLY — never a last-line/chrome
    # preview that could smuggle model voice past the court gate.
    with tempfile.TemporaryDirectory() as base:
        snapshot.write_status_snapshot(base, {"gm": {
            "lineage_root": "r", "runtime": "claude", "status": "idle",
            "text": "MODEL VOICE LEAK", "last_line": "MODEL VOICE LEAK",
            "chrome": {"preview": "MODEL VOICE LEAK"}}})
        blob = open(os.path.join(snapshot.realtime_dir(base), "status.json")).read()
        assert "MODEL VOICE LEAK" not in blob
        seat = snapshot.read_status_snapshot(base)["seats"]["gm"]
        assert set(seat) <= {"session", "lineage_root", "runtime", "status", "ts"}


def test_delta_path_sanitizes_session_id_no_traversal():
    with tempfile.TemporaryDirectory() as base:
        # a hostile session id must not escape the deltas/ dir
        snapshot.append_delta(base, "../../etc/evil", "x", seq=1)
        deltas = os.path.realpath(os.path.join(snapshot.realtime_dir(base), "deltas"))
        # nothing written outside deltas/
        assert not os.path.exists(os.path.join(base, "etc"))
        # the file that IS written is a flat name whose realpath stays INSIDE
        # deltas/ (no `/` in the name, no traversal out of the dir)
        names = os.listdir(deltas)
        assert names
        for n in names:
            assert "/" not in n
            assert os.path.realpath(os.path.join(deltas, n)).startswith(deltas + os.sep)


def test_delta_log_is_bounded_drop_oldest():
    with tempfile.TemporaryDirectory() as base:
        for i in range(1, snapshot.MAX_DELTA_FRAMES + 51):
            snapshot.append_delta(base, "gm", f"line-{i}", seq=i)
        frames = snapshot.read_delta_frames(base, "gm", after_seq=0)
        assert len(frames) <= snapshot.MAX_DELTA_FRAMES
        # oldest dropped, newest retained
        assert frames[-1]["text"] == f"line-{snapshot.MAX_DELTA_FRAMES + 50}"


def test_realtime_dir_honors_env_override(monkeypatch=None):
    # ORCHESTRA_REALTIME_DIR wins over the ~/.orchestra default.
    with tempfile.TemporaryDirectory() as base:
        os.environ["ORCHESTRA_REALTIME_DIR"] = base
        try:
            assert snapshot.realtime_dir() == base
        finally:
            del os.environ["ORCHESTRA_REALTIME_DIR"]
