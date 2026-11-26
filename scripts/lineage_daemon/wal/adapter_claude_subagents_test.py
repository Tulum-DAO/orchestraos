"""Subagent transcripts into the WAL sidecar (gm msg_d0b7b58b item 2).

Shapes as measured on pm-shoreline 2026-11-04 (Claude Code 2.1.284): <sid>/subagents/agent-<id>.jsonl
with isSidechain/agentId on every line, .meta.json {agentType, parentAgentId (depth >= 2), spawnDepth}.
"""
import json
import os
import sqlite3

import pytest

from .adapter_claude import ClaudeWalAdapter, SUBAGENT_DISABLE_FILE
from .store import WalStore

SID = "aaaaaaaa-1111-2222-3333-444444444444"


def _line(kind, ts, content, agent=None, usage=None, mid=None):
    rec = {"type": kind, "sessionId": SID, "timestamp": ts,
           "message": {"role": kind, "content": content}}
    if agent:
        rec.update(isSidechain=True, agentId=agent)
    if usage:
        rec["message"]["usage"] = usage
        rec["message"]["id"] = mid
    return json.dumps(rec) + "\n"


U = {"input_tokens": 3, "cache_read_input_tokens": 900000, "cache_creation_input_tokens": 7, "output_tokens": 2}


@pytest.fixture
def seat(tmp_path):
    proj = tmp_path / "projects" / "-x"
    proj.mkdir(parents=True)
    main = proj / f"{SID}.jsonl"
    main.write_text(_line("user", "2026-11-04T20:00:00Z", "go")
                    + _line("assistant", "2026-11-04T20:00:01Z",
                            [{"type": "tool_use", "id": "t0", "name": "Agent", "input": {}}],
                            usage={"input_tokens": 1, "cache_read_input_tokens": 10,
                                   "cache_creation_input_tokens": 1, "output_tokens": 1}, mid="m0"))
    sub = proj / SID / "subagents"
    sub.mkdir(parents=True)

    def agent(aid, depth, lines, parent=None, kind="general-purpose"):
        (sub / f"agent-{aid}.jsonl").write_text("".join(lines))
        meta = {"agentType": kind, "description": aid, "spawnDepth": depth}
        if parent:
            meta["parentAgentId"] = parent
        (sub / f"agent-{aid}.meta.json").write_text(json.dumps(meta))
        return sub / f"agent-{aid}.jsonl"

    p = agent("P", 1, [_line("user", "2026-11-04T20:00:02Z", "build", agent="P"),
                       _line("assistant", "2026-11-04T20:00:03Z",
                             [{"type": "tool_use", "id": "tp", "name": "Agent", "input": {}}],
                             agent="P", usage=U, mid="p1")])
    h = agent("H", 2, [_line("user", "2026-11-04T20:00:04Z", "task 5", agent="H"),
                       _line("assistant", "2026-11-04T20:00:05Z",
                             [{"type": "tool_use", "id": "th", "name": "Bash", "input": {}}],
                             agent="H", usage=U, mid="h1")], parent="P")
    store = WalStore(str(tmp_path / "wal" / "seat.db"))
    ad = ClaudeWalAdapter(store, "seat", 7)
    return dict(main=str(main), p=p, h=h, store=store, ad=ad, tmp=tmp_path)


def _rows(store, table):
    return [dict(r) for r in store._conn.execute(f"SELECT * FROM {table} ORDER BY seq")]


def test_subagent_rows_land_in_the_sidecar_with_who_wrote_them(seat):
    n = seat["ad"].tail(seat["main"])
    main_rows = _rows(seat["store"], "wal_events")
    assert n == len(main_rows) == 3                       # prompt, tool_call, ctx: the seat only
    assert all(r["source_path"] == seat["main"] for r in main_rows)
    sub = _rows(seat["store"], "wal_subagent_events")
    by = {}
    for r in sub:
        by.setdefault(r["agent_id"], []).append(r)
    assert set(by) == {"P", "H"}
    h = by["H"]
    assert {r["parent_agent_id"] for r in h} == {"P"} and {r["depth"] for r in h} == {2}
    assert {r["agent_type"] for r in h} == {"general-purpose"} and {r["generation"] for r in h} == {7}
    assert [r["kind"] for r in h] == ["prompt", "tool_call", "ctx"]
    assert h[1]["summary"] == "Bash" and h[1]["body_ref"] == f"{seat['h']}:{len(open(seat['h']).readline())}#c0"
    p = by["P"]
    assert {r["parent_agent_id"] for r in p} == {None} and {r["depth"] for r in p} == {1}


def test_the_blue_ctx_stays_main_transcript_only(seat):
    """The rotation engine's ctx reads wal_events: a subagent's 900K cache read must not appear there."""
    seat["ad"].tail(seat["main"])
    ctx = [r["summary"] for r in _rows(seat["store"], "wal_events") if r["kind"] == "ctx"]
    assert ctx == ["jsonl_tokens=12 out=1"]


def test_subagents_are_read_while_the_main_transcript_is_silent(seat):
    seat["ad"].tail(seat["main"])
    before = len(_rows(seat["store"], "wal_subagent_events"))
    with open(seat["h"], "a") as f:
        f.write(_line("user", "2026-11-04T20:00:06Z",
                      [{"type": "tool_result", "tool_use_id": "th", "content": "ok"}], agent="H"))
        f.write('{"type": "assistant", "half')         # a half-written line is not consumed
    assert seat["ad"].tail(seat["main"]) == 0             # nothing new in the seat's own file
    sub = _rows(seat["store"], "wal_subagent_events")
    assert len(sub) == before + 1 and sub[-1]["kind"] == "tool_result" and sub[-1]["agent_id"] == "H"
    cur = seat["store"].get_subagent_cursor(str(seat["h"]))
    with open(seat["h"], "rb") as f:
        assert f.read()[cur["last_off"]:] == b'{"type": "assistant", "half'
    # a fresh adapter (restart) re-reads nothing
    ClaudeWalAdapter(seat["store"], "seat", 7).tail(seat["main"])
    assert len(_rows(seat["store"], "wal_subagent_events")) == before + 1


def test_the_sidecar_is_append_only(seat):
    seat["ad"].tail(seat["main"])
    with pytest.raises(sqlite3.IntegrityError):
        seat["store"]._conn.execute("UPDATE wal_subagent_events SET summary='x'")
    with pytest.raises(sqlite3.IntegrityError):
        seat["store"]._conn.execute("DELETE FROM wal_subagent_events")


def test_a_failing_subagent_pass_never_fails_the_main_tail(seat, monkeypatch):
    def boom(self, path):
        raise RuntimeError("subagent pass blew up")
    monkeypatch.setattr(ClaudeWalAdapter, "_tail_subagents", boom)
    assert seat["ad"].tail(seat["main"]) == 3
    assert seat["store"].get_cursor(seat["main"])["last_off"] == os.path.getsize(seat["main"])


def test_kill_switch_stops_only_the_subagent_pass(seat):
    open(os.path.join(os.path.dirname(seat["store"].path), SUBAGENT_DISABLE_FILE), "w").close()
    assert seat["ad"].tail(seat["main"]) == 3
    assert _rows(seat["store"], "wal_subagent_events") == []


def test_a_store_without_the_sidecar_still_tails_the_seat(seat):
    class Plain:                                           # the shape older fakes have
        def __init__(self, s):
            self._s = s

        def __getattr__(self, k):
            if k in ("append_subagent", "path"):
                raise AttributeError(k)
            return getattr(self._s, k)
    ad = ClaudeWalAdapter(Plain(seat["store"]), "seat", 7)
    assert ad.tail(seat["main"]) == 3 and _rows(seat["store"], "wal_subagent_events") == []


def test_unchanged_files_cost_no_sql(seat, monkeypatch):
    seat["ad"].tail(seat["main"])
    calls = []
    real = seat["store"].get_subagent_cursor
    monkeypatch.setattr(seat["store"], "get_subagent_cursor", lambda p: (calls.append(p), real(p))[1])
    seat["ad"].tail(seat["main"])
    assert calls == []
    with open(seat["h"], "a") as f:
        f.write(_line("user", "2026-11-04T20:00:07Z", "more", agent="H"))
    seat["ad"].tail(seat["main"])
    assert calls == [str(seat["h"])]


# ---- codex r1 (REVISE, 6) --------------------------------------------------------------------------------

from . import adapter_claude as AC  # noqa: E402


def _count(store, agent=None):
    return len([r for r in _rows(store, "wal_subagent_events") if agent is None or r["agent_id"] == agent])


def test_r1_events_and_cursor_commit_together(seat, monkeypatch):
    real = seat["store"].set_subagent_cursor
    state = {"armed": True}

    def flaky(path, *a, **k):
        if state["armed"] and path.endswith("agent-H.jsonl"):
            state["armed"] = False
            raise OSError("crash between the line's events and its cursor")
        return real(path, *a, **k)
    monkeypatch.setattr(seat["store"], "set_subagent_cursor", flaky)
    seat["ad"].tail(seat["main"])
    seat["ad"].tail(seat["main"])
    h = [r for r in _rows(seat["store"], "wal_subagent_events") if r["agent_id"] == "H"]
    assert [r["kind"] for r in h] == ["prompt", "tool_call", "ctx"]          # once each, nothing doubled


def test_r1_a_replaced_or_truncated_file_is_read_from_the_start(seat):
    seat["ad"].tail(seat["main"])
    before = _count(seat["store"], "H")
    body = open(seat["h"]).read()
    with open(str(seat["h"]) + ".new", "w") as f:     # same path, same size, new inode (written
        f.write(body)                                  # while the old one exists, so no inode reuse)
    os.replace(str(seat["h"]) + ".new", seat["h"])
    seat["ad"].tail(seat["main"])
    assert _count(seat["store"], "H") == 2 * before
    with open(seat["h"], "w") as f:                    # truncated in place
        f.write(_line("user", "2026-11-04T21:00:00Z", "fresh", agent="H"))
    seat["ad"].tail(seat["main"])
    assert _count(seat["store"], "H") == 2 * before + 1


def test_r1_a_poisoned_line_or_file_never_blocks_later_files(seat, monkeypatch):
    sub = os.path.dirname(seat["h"])
    with open(os.path.join(sub, "agent-A.jsonl"), "w") as f:      # sorts before H and P
        f.write("[]\n" + _line("user", "2026-11-04T20:00:00Z", "x", agent="A"))
    with open(os.path.join(sub, "agent-A.meta.json"), "w") as f:
        json.dump({"agentType": "Explore", "spawnDepth": 1}, f)
    seat["ad"].tail(seat["main"])
    a = [r for r in _rows(seat["store"], "wal_subagent_events") if r["agent_id"] == "A"]
    assert [r["summary"] for r in a][:1] == ["non-object-line"] and _count(seat["store"], "H") == 3
    # a file whose pass raises outright: the others still go
    real_run = AC._SubagentTail.run

    def run(self, st):
        if self._agent_id == "B":
            raise RuntimeError("poisoned")
        return real_run(self, st)
    monkeypatch.setattr(AC._SubagentTail, "run", run)
    with open(os.path.join(sub, "agent-B.jsonl"), "w") as f:
        f.write(_line("user", "2026-11-04T20:00:00Z", "x", agent="B"))
    with open(seat["p"], "a") as f:
        f.write(_line("user", "2026-11-04T20:01:00Z", "more", agent="P"))
    seat["ad"].tail(seat["main"])
    assert _count(seat["store"], "P") == 4


def test_r1_a_transcript_waits_for_its_meta_before_its_first_row(seat):
    sub = os.path.dirname(seat["h"])
    j = os.path.join(sub, "agent-N.jsonl")
    with open(j, "w") as f:
        f.write(_line("user", "2026-11-04T20:00:00Z", "x", agent="N"))
    seat["ad"].tail(seat["main"])
    assert _count(seat["store"], "N") == 0                       # young, no meta yet
    with open(os.path.join(sub, "agent-N.meta.json"), "w") as f:
        json.dump({"agentType": "Explore", "spawnDepth": 2, "parentAgentId": "P"}, f)
    seat["ad"].tail(seat["main"])
    n = [r for r in _rows(seat["store"], "wal_subagent_events") if r["agent_id"] == "N"]
    assert len(n) == 1 and n[0]["depth"] == 2 and n[0]["parent_agent_id"] == "P"
    # an OLD file that never got a meta is captured anyway, with nulls
    o = os.path.join(sub, "agent-O.jsonl")
    with open(o, "w") as f:
        f.write(_line("user", "2026-11-04T20:00:00Z", "x", agent="O"))
    old = os.path.getmtime(o) - AC.SUBAGENT_META_WAIT_S - 5
    os.utime(o, (old, old))
    seat["ad"].tail(seat["main"])
    o_rows = [r for r in _rows(seat["store"], "wal_subagent_events") if r["agent_id"] == "O"]
    assert len(o_rows) == 1 and o_rows[0]["depth"] is None


def test_r1_a_backlog_drains_in_bounded_reads_without_loss_or_doubles(seat, monkeypatch):
    monkeypatch.setattr(AC, "SUBAGENT_READ_MAX", 300)
    with open(seat["h"], "a") as f:
        for i in range(20):
            f.write(_line("user", f"2026-11-04T20:10:{i:02d}Z", f"line {i}", agent="H"))
    for _ in range(40):
        seat["ad"].tail(seat["main"])
    assert _count(seat["store"], "H") == 3 + 20
    assert seat["store"].get_subagent_cursor(str(seat["h"]))["last_off"] == os.path.getsize(seat["h"])


def test_r1_a_broken_stderr_does_not_turn_a_warning_into_a_raise(seat, monkeypatch):
    class Bad:
        def write(self, *_):
            raise OSError("stderr closed")

        def flush(self):
            raise OSError("stderr closed")
    monkeypatch.setattr(ClaudeWalAdapter, "_tail_subagents",
                        lambda self, p: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(AC.sys, "stderr", Bad())
    assert seat["ad"].tail(seat["main"]) == 3


def test_r2_an_active_file_without_meta_still_gets_captured_after_the_wait(seat, monkeypatch):
    sub = os.path.dirname(seat["h"])
    j = os.path.join(sub, "agent-Q.jsonl")
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(AC.time if hasattr(AC, "time") else __import__("time"), "time", lambda: clock["t"])
    for i in range(3):                                 # written every minute: mtime keeps moving
        with open(j, "a") as f:
            f.write(_line("user", "2026-11-04T20:00:00Z", f"x{i}", agent="Q"))
        os.utime(j, (clock["t"], clock["t"]))
        seat["ad"].tail(seat["main"])
        clock["t"] += 61
    assert _count(seat["store"], "Q") == 3
