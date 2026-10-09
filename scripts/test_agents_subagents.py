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
            return {"state": "working", "process": {"runtime": rt, "cpu": 1.0}}

    monkeypatch.setattr(wg, "ORCH_DIR", tmp_path / "orch")
    monkeypatch.setattr(wg, "_agent_status", lambda: _AS())
    monkeypatch.setattr(wg, "_tmux_session_names", lambda: ["seat", "gem"])
    monkeypatch.setattr(wg, "_live_voice_call", lambda: None)
    by = {r["tmux_session"]: r for r in wg.compute_agents()}
    assert by["seat"]["provider"] == "claude" and by["seat"]["subagents"] == 1
    assert by["gem"]["provider"] == "gemini" and by["gem"]["subagents"] == 0
