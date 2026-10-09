"""/agents rows carry `subagents`: how many subagents the seat's Claude session is running now.

For the Quest's subagent "moons": each /agents row says how many subagents its seat is running.
A subagent file <session dir>/subagents/agent-*.jsonl is RUNNING unless its last record is an
assistant message that stopped with end_turn/stop_sequence, or the file is >15 min old.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watch_gateway as wg  # noqa: E402


def _rec(kind, stop=None):
    if kind == "assistant":
        return {"type": "assistant", "message": {"role": "assistant", "stop_reason": stop,
                                                 "content": [{"type": "text", "text": "x"}]}}
    if kind == "attachment":
        return {"type": "attachment", "attachment": {"type": "date"}}
    return {"type": "user", "message": {"role": "user", "content": "go"}}


def _session(tmp_path, agents, now):
    """agents: {name: (records, age_s)}. Returns the transcript path the hook event names."""
    proj = tmp_path / ".claude" / "projects" / "-x"
    sub = proj / "sid1" / "subagents"
    sub.mkdir(parents=True)
    for name, (recs, age) in agents.items():
        p = sub / f"agent-{name}.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in recs))
        os.utime(p, (now - age, now - age))
        (sub / f"agent-{name}.meta.json").write_text("{}")   # never counted
    tp = proj / "sid1.jsonl"
    tp.write_text("")
    return str(tp)


def setup_function(_):
    wg._SUBAGENT_TAIL_CACHE.clear()


def test_counts_only_running_subagents(tmp_path):
    now = time.time()
    tp = _session(tmp_path, {
        "tooling": ([_rec("user"), _rec("assistant", "tool_use"), _rec("user")], 5),
        "streaming": ([_rec("user"), _rec("assistant", None)], 5),
        "done": ([_rec("user"), _rec("assistant", "end_turn")], 5),
        "done_seq": ([_rec("user"), _rec("assistant", "stop_sequence")], 5),
        "stale": ([_rec("user"), _rec("assistant", "tool_use")], 16 * 60),
    }, now)
    assert wg.count_running_subagents(tp, now=now) == 2


def test_trailing_attachment_after_final_answer_is_not_running(tmp_path):
    """Measured on a live fleet 2026-10-08: 2 of 300 finished subagent files end with an
    `attachment` record written after the final end_turn answer."""
    now = time.time()
    tp = _session(tmp_path, {
        "done": ([_rec("user"), _rec("assistant", "end_turn"), _rec("attachment")], 5),
    }, now)
    assert wg.count_running_subagents(tp, now=now) == 0


def test_unresolved_session_is_zero(tmp_path):
    assert wg.count_running_subagents(None) == 0
    assert wg.count_running_subagents("") == 0
    assert wg.count_running_subagents(str(tmp_path / "nope.jsonl")) == 0


def test_a_finishing_subagent_is_reread_not_served_from_cache(tmp_path):
    now = time.time()
    tp = _session(tmp_path, {"a": ([_rec("user"), _rec("assistant", "tool_use")], 5)}, now)
    assert wg.count_running_subagents(tp, now=now) == 1
    p = tmp_path / ".claude" / "projects" / "-x" / "sid1" / "subagents" / "agent-a.jsonl"
    with open(p, "a") as f:
        f.write(json.dumps(_rec("assistant", "end_turn")) + "\n")
    os.utime(p, (now, now))
    assert wg.count_running_subagents(tp, now=now) == 0


def test_agents_rows_carry_subagents(tmp_path, monkeypatch):
    """The real compute_agents recency loop stamps the field from the pane's hook event."""
    now = time.time()
    tp = _session(tmp_path, {"a": ([_rec("user"), _rec("assistant", "tool_use")], 5)}, now)
    rows = [
        {"id": "seat", "state": "working", "tmux_session": "seat", "provider": "claude"},
        {"id": "gem", "state": "working", "tmux_session": "gem", "provider": "gemini"},
        {"id": "off", "state": "offline", "tmux_session": "off", "provider": None},
    ]
    events = {"seat": {"ts": now, "transcript_path": tp},
              "gem": {"ts": now, "transcript_path": tp}}
    wg._stamp_recency_and_subagents(rows, lambda sess: events.get(sess), now=now)
    by = {r["id"]: r for r in rows}
    assert by["seat"]["subagents"] == 1
    assert by["gem"]["subagents"] == 0      # non-Claude seat: always 0
    assert by["off"]["subagents"] == 0
    assert by["seat"]["last_used_ts"] == now


def test_compute_agents_reports_provider_and_subagents_end_to_end(tmp_path, monkeypatch):
    """The real compute_agents: `provider` comes from the detector's process scan, and a Claude
    seat's running subagents are counted through its pane's hook event."""
    now = time.time()
    tp = _session(tmp_path, {"a": ([_rec("user"), _rec("assistant", "tool_use")], 5)}, now)
    panes = tmp_path / "orch" / "state" / "agent-events" / "panes"
    panes.mkdir(parents=True)
    (panes / "1.json").write_text(json.dumps({"ts": now, "transcript_path": tp}))
    (panes / "2.json").write_text(json.dumps({"ts": now, "transcript_path": tp}))

    class _AS:
        def get_pane_id(self, sess):
            return {"seat": "%1", "gem": "%2"}[sess]

        def get_agent_status(self, sess):
            rt = {"seat": "claude", "gem": "gemini"}[sess]
            # a real pid that started before the hook files were written (this test process)
            return {"state": "working", "process": {"runtime": rt, "cpu": 1.0, "pid": os.getpid()}}

    monkeypatch.setattr(wg, "ORCH_DIR", tmp_path / "orch")
    monkeypatch.setattr(wg, "_agent_status", lambda: _AS())
    monkeypatch.setattr(wg, "_tmux_session_names", lambda: ["seat", "gem"])
    monkeypatch.setattr(wg, "_live_voice_call", lambda: None)
    by = {r["tmux_session"]: r for r in wg.compute_agents()}
    assert by["seat"]["provider"] == "claude" and by["seat"]["subagents"] == 1
    assert by["gem"]["provider"] == "gemini" and by["gem"]["subagents"] == 0


def test_a_final_answer_longer_than_the_first_tail_is_finished(tmp_path):
    """Measured on a live fleet: 145 of 611 finished files end in a final answer over 8 KB."""
    now = time.time()
    long_answer = _rec("assistant", "end_turn")
    long_answer["message"]["content"][0]["text"] = "x" * 40_000
    tp = _session(tmp_path, {
        "long": ([_rec("user")] + [_rec("assistant", "tool_use"), _rec("user")] * 50 + [long_answer], 5),
        "long_then_attach": ([_rec("user"), long_answer] + [_rec("attachment")] * 3, 5),
    }, now)
    assert wg.count_running_subagents(tp, now=now) == 0


def test_a_record_too_long_for_every_tail_reads_as_running_until_stale(tmp_path):
    now = time.time()
    huge = _rec("assistant", "end_turn")
    huge["message"]["content"][0]["text"] = "x" * (300 * 1024)
    tp = _session(tmp_path, {"huge": ([_rec("user"), huge], 5)}, now)
    assert wg.count_running_subagents(tp, now=now) == 1


def test_the_tail_cache_drops_entries_nobody_scans(tmp_path, monkeypatch):
    monkeypatch.setattr(wg, "_SUBAGENT_TAIL_CACHE_MAX", 2)
    now = time.time()
    for i in range(5):
        wg._SUBAGENT_TAIL_CACHE[f"/gone/{i}"] = (now - wg.SUBAGENT_STALE_S - 10, 1, True)
    tp = _session(tmp_path, {"a": ([_rec("user"), _rec("assistant", "tool_use")], 5)}, now)
    wg.count_running_subagents(tp, now=now)
    assert not [p for p in wg._SUBAGENT_TAIL_CACHE if p.startswith("/gone/")]



def _fleet(tmp_path, monkeypatch, event_ts, pid):
    now = time.time()
    tp = _session(tmp_path, {"a": ([_rec("user"), _rec("assistant", "tool_use")], 5)}, now)
    panes = tmp_path / "orch" / "state" / "agent-events" / "panes"
    panes.mkdir(parents=True)
    (panes / "1.json").write_text(json.dumps({"ts": event_ts, "transcript_path": tp}))

    class _AS:
        def get_pane_id(self, sess):
            return "%1"

        def get_agent_status(self, sess):
            return {"state": "idle", "process": {"runtime": "claude", "cpu": 0.0, "pid": pid}}

    monkeypatch.setattr(wg, "ORCH_DIR", tmp_path / "orch")
    monkeypatch.setattr(wg, "_agent_status", lambda: _AS())
    monkeypatch.setattr(wg, "_tmux_session_names", lambda: ["svc"])
    monkeypatch.setattr(wg, "_live_voice_call", lambda: None)
    return {r["tmux_session"]: r for r in wg.compute_agents()}["svc"]


def test_a_hook_file_older_than_the_panes_agent_is_not_its_recency(tmp_path, monkeypatch):
    """tmux reuses pane ids after a restart, so a pane can carry another session's old hook file."""
    started = wg._proc_start_epoch(os.getpid())
    row = _fleet(tmp_path, monkeypatch, event_ts=started - 3600, pid=os.getpid())
    assert row["last_used_ts"] == 0.0 and row["subagents"] == 0


def test_a_pane_with_no_agent_process_has_no_hook_recency(tmp_path, monkeypatch):
    row = _fleet(tmp_path, monkeypatch, event_ts=time.time(), pid=None)
    assert row["last_used_ts"] == 0.0


def test_a_current_hook_file_counts(tmp_path, monkeypatch):
    ts = time.time()
    row = _fleet(tmp_path, monkeypatch, event_ts=ts, pid=os.getpid())
    assert row["last_used_ts"] == ts and row["subagents"] == 1


def test_process_start_is_read_from_proc():
    started = wg._proc_start_epoch(os.getpid())
    assert started is not None and time.time() - 86400 * 30 < started <= time.time()
    assert wg._proc_start_epoch(None) is None and wg._proc_start_epoch(2 ** 30) is None
