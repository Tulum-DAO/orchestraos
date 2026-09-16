"""RED tests — the seat-builder (the missing glue: roster+live-tmux -> seats).

Pure enumeration/skip logic with INJECTED resolvers (the live resolvers — pane pid,
per-runtime source path, hooks, ring — are thin defaults exercised only live). It
produces realtime Seats (need root_pid) and durable MuxSeats (need source_path);
fail-soft per seat: a seat missing its required field is skipped, never wedges the
build. lineage_root falls back to the agent id (the fleet convention).
"""
from .seat_builder import build_seats


def _reg(**over):
    base = {"lineage_root": "root-gm", "session_id": "sid-gm", "runtime": "claude",
            "generation": 3, "cwd": "/home/testuser"}
    base.update(over)
    return base


AGENTS = {
    "gm": _reg(),
    "codex-dev-1": _reg(lineage_root="root-cx", session_id="sid-cx", runtime="codex"),
    "dead-seat": _reg(lineage_root="root-dead"),      # in roster but NOT live
    "no-lineage": {"session_id": "sid-nl", "runtime": "claude", "cwd": "/x"},  # lineage absent
}
LIVE = {"gm", "codex-dev-1", "no-lineage"}            # dead-seat is not in the live set


def _resolvers(pids=None, sources=None):
    pids = pids or {"gm": 100, "codex-dev-1": 200, "no-lineage": 300}
    sources = sources or {"gm": "/c/gm.jsonl", "codex-dev-1": "/x/rollout.jsonl",
                          "no-lineage": "/c/nl.jsonl"}
    return dict(
        pane_pid=lambda s: pids.get(s),
        source_path=lambda s, record, pid: sources.get(s),
        hooks_for=lambda s, runtime: {"state": "working"} if runtime == "claude" else None,
        ring_for=lambda s: object(),
        resolve_generation=lambda s, record: record.get("generation") or 1,
    )


def test_only_live_sessions_become_seats():
    seats, mux = build_seats(AGENTS, LIVE, **_resolvers())
    sessions = {s.session for s in seats}
    assert sessions == {"gm", "codex-dev-1", "no-lineage"}
    assert "dead-seat" not in sessions               # roster but not live -> excluded


def test_lineage_root_falls_back_to_agent_id():
    seats, mux = build_seats(AGENTS, LIVE, **_resolvers())
    nl = next(s for s in seats if s.session == "no-lineage")
    assert nl.lineage_root == "no-lineage"           # registry lacked it -> agent id


def test_runtime_carried_through():
    seats, _ = build_seats(AGENTS, LIVE, **_resolvers())
    rt = {s.session: s.runtime for s in seats}
    assert rt["gm"] == "claude" and rt["codex-dev-1"] == "codex"


def test_seat_without_pane_pid_is_skipped_fail_soft():
    r = _resolvers(pids={"gm": 100, "codex-dev-1": 200})   # no-lineage pid missing
    seats, _ = build_seats(AGENTS, LIVE, **r)
    assert {s.session for s in seats} == {"gm", "codex-dev-1"}   # skipped, not raised


def test_mux_seat_without_source_path_is_skipped():
    r = _resolvers(sources={"gm": "/c/gm.jsonl"})    # only gm resolves a source
    _, mux = build_seats(AGENTS, LIVE, **r)
    assert {m.lineage_root for m in mux} == {"root-gm"}


def test_claude_gets_hooks_codex_does_not():
    seats, _ = build_seats(AGENTS, LIVE, **_resolvers())
    by = {s.session: s for s in seats}
    assert by["gm"].hooks == {"state": "working"}
    assert by["codex-dev-1"].hooks is None


# --- build_live_daemon regression: env-string ORCHESTRA_DIR must not crash -----
# (Gate-2 crash-loop: TypeError str/str at load_stores because build_live_daemon
# handed it the plain-str env var. These exercise the previously pragma-no-cover
# live path via an injected resolver bundle — the exact prod config.)

class _DummyTmux:
    def list_sessions(self):
        return []
    def pipe_pane(self, session, sink):
        pass


def _fake_resolvers(load_stores):
    from .seat_builder import _Resolvers
    return _Resolvers(
        load_stores=load_stores, live_sessions=lambda: set(),
        pane_pid=lambda s: None, live_resolver=lambda sessions: {},
        source_path=lambda s, r, p: None,
        hooks_for=lambda s, rt: None, ring_for=lambda s: None, tmux=_DummyTmux(),
        resolve_generation=lambda s, r: 1)


def test_build_live_daemon_passes_a_path_not_the_str_env(monkeypatch, tmp_path):
    from pathlib import Path
    from .seat_builder import build_live_daemon
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))   # PROD CONFIG: env is a STR
    seen = {}

    def fake_load_stores(orch):
        seen["orch"] = orch
        _ = orch / "state"                                # mirrors load_stores' first act
        return ({}, {})

    d = build_live_daemon(resolvers=_fake_resolvers(fake_load_stores))
    assert d is not None                                   # must NOT raise TypeError
    assert isinstance(seen["orch"], Path)                 # got a Path, not a bare str
    assert not isinstance(seen["orch"], str)


def test_build_live_daemon_flag_store_points_at_the_flags_file(monkeypatch, tmp_path):
    from .seat_builder import build_live_daemon
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    d = build_live_daemon(resolvers=_fake_resolvers(lambda o: ({}, {})))
    # the flag store must point at the lineage_flags.json FILE, never the wal_dir
    assert d.flag_store._path.endswith("lineage_flags.json")
    assert d.flag_store._path != str(tmp_path / "state" / "wal")


def test_read_pane_hooks_reads_event_file_by_pane_id_no_subprocess(tmp_path):
    # the cheap half of the per-tick hook refresh: read the hook dict DIRECTLY from
    # the pane_id's event file (pane_id.lstrip('%') + '.json'), no tmux subprocess.
    import json as _json
    from .seat_builder import read_pane_hooks
    # event files are named by the pane_id with the leading '%' STRIPPED (mirrors
    # agent_status._read_hook_event: pane_id.lstrip('%') + '.json').
    (tmp_path / "5.json").write_text(_json.dumps({"state": "working", "event": "PreToolUse"}))
    assert read_pane_hooks("%5", str(tmp_path)) == {"state": "working", "event": "PreToolUse"}


def test_read_pane_hooks_none_for_absent_malformed_or_stateless(tmp_path):
    import json as _json
    from .seat_builder import read_pane_hooks
    assert read_pane_hooks("%404", str(tmp_path)) is None          # no file (non-claude pane)
    assert read_pane_hooks(None, str(tmp_path)) is None            # no pane_id
    (tmp_path / "6.json").write_text("{ not json")                 # malformed
    assert read_pane_hooks("%6", str(tmp_path)) is None
    (tmp_path / "7.json").write_text(_json.dumps({"event": "x"}))  # dict but no 'state'
    assert read_pane_hooks("%7", str(tmp_path)) is None


def test_turn_resolver_reads_transcript_from_hook_session_and_cwd(tmp_path, monkeypatch):
    # E2 wiring: the live turn resolver derives the claude .jsonl path from the LIVE
    # hook's session_id + cwd (the same slug convention as source_path) and returns
    # the transcript's turn-completion — no extra tmux/registry lookup.
    import json as _json
    from .seat_builder import build_turn_resolver
    # a fake claude project dir with a completed-turn transcript
    proj = tmp_path / ".claude" / "projects" / "-home-testuser-agent-orchestra"
    proj.mkdir(parents=True)
    sid = "abc-123"
    (proj / f"{sid}.jsonl").write_text(_json.dumps(
        {"type": "assistant", "message": {"role": "assistant",
         "stop_reason": "end_turn", "content": [{"type": "text", "text": "done"}]}}) + "\n")
    monkeypatch.setattr("os.path.expanduser",
                        lambda p: p.replace("~", str(tmp_path)))
    r = build_turn_resolver()
    hooks = {"state": "working", "session_id": sid, "cwd": "/home/testuser/agent-orchestra"}
    assert r("cli-chrome", "claude", hooks) is True          # completed turn -> idle-worthy


def test_turn_resolver_none_for_non_claude_or_missing_fields(tmp_path):
    from .seat_builder import build_turn_resolver
    r = build_turn_resolver()
    assert r("codex-dev", "codex", {"state": "working"}) is None      # non-claude
    assert r("s", "claude", None) is None                            # no hook dict
    assert r("s", "claude", {"state": "working"}) is None            # no session_id/cwd


def test_build_live_daemon_wires_the_turn_resolver(monkeypatch, tmp_path):
    from .seat_builder import _Resolvers, build_live_daemon
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    r = _Resolvers(
        load_stores=lambda o: ({}, {}), live_sessions=lambda: set(),
        pane_pid=lambda s: None, live_resolver=lambda sessions: {},
        source_path=lambda s, r, p: None, hooks_for=lambda s, rt: None,
        ring_for=lambda s: None, tmux=_DummyTmux(), resolve_generation=lambda s, r: 1)
    d = build_live_daemon(resolvers=r)
    assert d._turn_resolver is not None                     # E2 resolver wired, not None


def test_build_live_daemon_wires_the_batched_live_resolver_for_per_tick_refresh(monkeypatch, tmp_path):
    # The daemon must re-resolve root_pid + hooks per SLOW tick (kills the frozen
    # offline_crashed/stalled staleness) — so build_live_daemon must hand the BATCHED
    # live_resolver to the TelemetryDaemon, not leave it None.
    from .seat_builder import _Resolvers, build_live_daemon
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    live_resolver = lambda sessions: {s: {"pid": 4242, "hooks": None} for s in sessions}
    r = _Resolvers(
        load_stores=lambda o: ({}, {}), live_sessions=lambda: set(),
        pane_pid=lambda s: None, live_resolver=live_resolver, source_path=lambda s, r, p: None,
        hooks_for=lambda s, rt: None, ring_for=lambda s: None, tmux=_DummyTmux(),
        resolve_generation=lambda s, r: 1)
    d = build_live_daemon(resolvers=r)
    assert d._live_resolver is live_resolver


# --- generation resolve-chain (DEC-1788481319: registry-int -> generations-by-sid
# [READ-ONLY] -> fallback 1; NEVER None into the NOT-NULL WAL; gen-1 = fleet convention)
import sqlite3


def _gen_db(rows):
    """in-memory generations table stand-in for the RO conn (condition A)."""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE generations (session_id TEXT UNIQUE, generation INTEGER)")
    for sid, gen in rows:
        c.execute("INSERT INTO generations (session_id, generation) VALUES (?,?)", (sid, gen))
    c.commit()
    return c


def test_generation_registry_int_wins():
    from .seat_builder import _resolve_generation
    assert _resolve_generation({"generation": 3, "session_id": "s"}, conn=None) == 3
    assert _resolve_generation({"generation": "5"}, conn=None) == 5   # str coerces


def test_generation_zero_treated_as_missing_then_fallback():
    from .seat_builder import _resolve_generation
    assert _resolve_generation({"generation": 0, "session_id": None}, conn=None) == 1


def test_generation_resolved_from_generations_table_by_sid():
    from .seat_builder import _resolve_generation
    conn = _gen_db([("sid-rotated", 4)])
    # registry None but the AUTHORITATIVE table has it -> use it (NOT fallback 1)
    assert _resolve_generation({"generation": None, "session_id": "sid-rotated"}, conn=conn) == 4


def test_generation_fallback_1_when_no_row_and_no_registry():
    from .seat_builder import _resolve_generation
    conn = _gen_db([("someone-else", 9)])
    assert _resolve_generation({"generation": None, "session_id": "unknown-sid"}, conn=conn) == 1
    assert _resolve_generation({"generation": None, "session_id": None}, conn=None) == 1


def test_generation_never_returns_none_or_zero_or_negative():
    from .seat_builder import _resolve_generation
    for rec in [{}, {"generation": None}, {"generation": "x"}, {"generation": -2}]:
        g = _resolve_generation(rec, conn=None)
        assert isinstance(g, int) and g >= 1


def test_ro_open_absent_db_returns_none_never_raises(tmp_path):
    from .seat_builder import _ro_open
    assert _ro_open(str(tmp_path / "nope.db")) is None          # absent -> None, no raise
    # a real db opens read-only
    import sqlite3 as s
    p = tmp_path / "reg.db"
    c = s.connect(str(p)); c.execute("CREATE TABLE generations (session_id TEXT, generation INTEGER)"); c.commit(); c.close()
    conn = _ro_open(str(p))
    assert conn is not None
    conn.close()


def test_build_seats_muxseat_generation_comes_from_resolver():
    # inject a resolve_generation; MuxSeat carries the resolved value, never None
    r = _resolvers()
    r["resolve_generation"] = lambda session, record: 7
    seats, mux = build_seats(AGENTS, LIVE, **r)
    assert mux and all(m.generation == 7 for m in mux)
