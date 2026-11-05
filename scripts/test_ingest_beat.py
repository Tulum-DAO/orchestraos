"""Saving terminal seats every minute (scripts/beats/ingest_beat.py) and the store pieces it rests on: the transcript
path cached in the cursor table, the per-seat ingest lock, and the purge a retired throwaway's rows go through.
Temp data dirs and a temp transcript root only."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture
def box(tmp_path, monkeypatch):
    orch, home = tmp_path / "d", tmp_path / "h"
    (orch / "state").mkdir(parents=True)
    projects = home / "projects" / "-work"
    projects.mkdir(parents=True)
    monkeypatch.setenv("ORCHESTRA_DIR", str(orch))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    import episodic_memory as em
    import importlib
    beat = importlib.import_module("beats.ingest_beat")
    return orch, projects, em, beat


def line(t, uuid, text, ts="2026-10-31T19:00:00Z", sid="s-term-1"):
    return json.dumps({"type": t, "uuid": uuid, "timestamp": ts, "sessionId": sid,
                       "message": {"role": t, "content": text}}) + "\n"


def registry(orch, rows):
    (orch / "registry.json").write_text(json.dumps({"agents": rows}))


def test_a_terminal_seat_is_registered_saved_and_a_turn_in_progress_waits_for_its_line(box):
    orch, projects, em, beat = box
    registry(orch, {"pm-demo": {"runtime": "claude", "session_id": "s-term-1", "tmux_session": "pm-demo"},
                    "qa-demo": {"runtime": "claude", "session_id": "s-hl", "tmux_session": "qa-demo"},
                    "dev-demo": {"runtime": "codex", "session_id": "s-cx"},
                    "old-demo": {"runtime": "claude", "session_id": "s-old", "status": "retired"}})
    import headless_switch as HS
    HS.set_switch("qa-demo", True, runtime="claude", orch_dir=orch)            # its supervisor saves it
    f = projects / "s-term-1.jsonl"
    f.write_text(line("user", "u1", "Sample ask") + line("assistant", "a1", "Sample answer"))
    with open(f, "a") as fh:
        fh.write(line("assistant", "a2", "half of a line")[:-20])               # a turn still being written
    out = beat.run(orch)
    assert [s["seat"] for s in out["seats"]] == ["pm-demo"], "terminal Claude seats only"
    s = out["seats"][0]
    assert s["registered"] and s["was_active"] is None and s["added"] == 2
    assert em.active_session("pm-demo")["provider_session_id"] == "s-term-1"
    with open(f, "a") as fh:
        fh.write(line("assistant", "a2", "half of a line")[-20:])               # the line completes
    again = beat.run(orch)["seats"][0]
    assert "registered" not in again and again["added"] == 1


def test_a_new_session_of_the_seat_becomes_the_active_one(box):
    orch, projects, em, beat = box
    registry(orch, {"pm-demo": {"runtime": "claude", "session_id": "s-term-1"}})
    (projects / "s-term-1.jsonl").write_text(line("user", "u1", "Sample one"))
    beat.run(orch)
    registry(orch, {"pm-demo": {"runtime": "claude", "session_id": "s-term-2"}})
    (projects / "s-term-2.jsonl").write_text(line("user", "u2", "Sample two", sid="s-term-2"))
    s = beat.run(orch)["seats"][0]
    assert s["registered"] and s["was_active"] == "s-term-1" and s["added"] >= 1
    assert em.active_session("pm-demo")["provider_session_id"] == "s-term-2"


def test_the_path_is_found_once_and_then_read_from_the_cursor(box, monkeypatch):
    orch, projects, em, beat = box
    registry(orch, {"pm-demo": {"runtime": "claude", "session_id": "s-term-1"}})
    (projects / "s-term-1.jsonl").write_text(line("user", "u1", "Sample one"))
    searched = []
    real = em.find_transcript
    monkeypatch.setattr(em, "find_transcript", lambda sid: searched.append(sid) or real(sid))
    beat.run(orch)
    beat.run(orch)
    beat.run(orch)
    assert searched == ["s-term-1"], "searched for once, then the cursor's path"
    (projects / "s-term-1.jsonl").rename(projects.parent / "moved.jsonl")
    moved = projects.parent / "-elsewhere"
    moved.mkdir()
    (projects.parent / "moved.jsonl").rename(moved / "s-term-1.jsonl")
    beat.run(orch)
    assert searched == ["s-term-1", "s-term-1"], "its file gone: searched for again"


def test_a_seat_whose_lock_is_held_is_skipped_this_minute(box):
    orch, projects, em, beat = box
    registry(orch, {"pm-demo": {"runtime": "claude", "session_id": "s-term-1"}})
    (projects / "s-term-1.jsonl").write_text(line("user", "u1", "Sample one"))
    import fcntl
    fh = open(em._lock_path("pm-demo", None), "a+")
    fcntl.flock(fh, fcntl.LOCK_EX)                                              # a rotation's export holds it
    try:
        s = beat.run(orch)["seats"][0]
        assert s["skipped"] == "busy" and s["added"] == 0
        t0 = time.monotonic()
        assert em.ingest_tick("pm-demo", wait_s=0.3)["skipped"] == "busy" and time.monotonic() - t0 < 1.5
    finally:
        fh.close()
    assert beat.run(orch)["seats"][0]["added"] == 1


def test_purge_removes_a_retired_agent_and_refuses_a_registered_one(box):
    orch, projects, em, beat = box
    registry(orch, {"pm-demo": {"runtime": "claude", "session_id": "s-term-1"}})
    (projects / "s-term-1.jsonl").write_text(line("user", "u1", "Sample one") + line("assistant", "a1", "Sample two"))
    beat.run(orch)
    em.mark_seen_only("s-term-1", "u1")
    em.set_seed_boundary("s-term-1", "u1")
    n = em.notice_start("pm-demo", "s-term-1", 10)
    em.record_attempt("pm-demo", "manual", "failed", "Sample reason", notice_id=n)
    with pytest.raises(em.StillRegistered):
        em.purge("pm-demo")
    registry(orch, {})
    before = em.counts_for("pm-demo")
    assert all(before[t] >= 1 for t in ("entries", "cursors", "provider_sessions", "seen_only", "seed_boundary",
                                       "compaction_notices", "compaction_attempts", "ingest_state")), before
    dry = em.purge("pm-demo", dry_run=True)
    assert {k: v for k, v in dry.items() if k != "embeddings"} == before, "a dry run counts what is there"
    assert em.counts_for("pm-demo") == before, "and deletes nothing"
    got = em.purge("pm-demo")
    assert got == dry and sum(em.counts_for("pm-demo").values()) == 0
    assert em.query("Sample", agent="pm-demo") == [], "its search rows are gone too"
    assert sum(em.purge("pm-demo").values()) == 0, "idempotent"


def test_purge_from_the_cli_says_what_it_would_do_and_needs_yes(box, capsys):
    orch, projects, em, beat = box
    registry(orch, {"pm-demo": {"runtime": "claude", "session_id": "s-term-1"}})
    (projects / "s-term-1.jsonl").write_text(line("user", "u1", "Sample one"))
    beat.run(orch)
    from orchestra_cli import memory_cmd
    assert memory_cmd.main(["purge", "pm-demo"]) == 2 and "still in the registry" in capsys.readouterr().err
    registry(orch, {})
    assert memory_cmd.main(["purge", "pm-demo"]) == 0
    assert "would delete" in capsys.readouterr().out and em.counts_for("pm-demo")["entries"] == 1
    assert memory_cmd.main(["purge", "pm-demo", "--yes", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["deleted"]["entries"] == 1
    assert memory_cmd.main(["counts", "pm-demo"]) == 0 and "(total 0)" in capsys.readouterr().out
