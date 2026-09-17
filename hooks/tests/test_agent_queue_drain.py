"""RED-first: the shipped idle-inbox drain resolves its stores from ORCHESTRA_DIR (the data
dir), defaults to ON for every registered seat when no allowlist file exists, and blocks the
Stop exactly once with a [QUEUE-DIGEST] when self-bound mail is pending."""
import json
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import importlib  # noqa: E402


def _mod():
    if "agent_queue_drain" in sys.modules:
        del sys.modules["agent_queue_drain"]
    spec = importlib.util.spec_from_file_location("agent_queue_drain", HERE.parent / "agent-queue-drain.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _data(tmp_path, agent="planner", pending=1, age_s=120):
    d = tmp_path / "data"; (d / "state").mkdir(parents=True)
    (d / "registry.json").write_text(json.dumps({"agents": {agent: {"name": agent, "tmux_session": agent}}}))
    db = d / "state" / "tasks.db"
    c = sqlite3.connect(db)
    c.execute("create table messages (id text primary key, from_agent text, to_agent text, type text, subject text, body text, status text, created_at text, archived_at text, metadata text)")
    c.execute("create table conversations (id text primary key)")
    ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - age_s))
    for i in range(pending):
        c.execute("insert into messages values (?,?,?,?,?,?,?,?,?,?)",
                  (f"msg_{i}", "gm", agent, "task_request", f"subject {i}", "body", "pending", ts, None, "{}"))
    c.commit(); c.close()
    return d


def test_drain_blocks_once_with_digest_from_orchestra_dir(tmp_path, monkeypatch):
    d = _data(tmp_path)
    monkeypatch.setenv("ORCHESTRA_DIR", str(d))
    m = _mod()
    env = {"AQD_SESSION": "planner"}
    out = m.run({"session_id": "s1", "stop_hook_active": False}, env=env)
    assert out and out["decision"] == "block" and "[QUEUE-DIGEST]" in out["reason"] and "msg_0" in out["reason"]
    # second stop with the same head: no re-block (marker)
    out2 = m.run({"session_id": "s1", "stop_hook_active": False}, env=env)
    assert not out2


def test_drain_silent_with_no_mail_and_fails_open_on_missing_stores(tmp_path, monkeypatch):
    d = _data(tmp_path, pending=0)
    monkeypatch.setenv("ORCHESTRA_DIR", str(d))
    m = _mod()
    assert not m.run({"session_id": "s1"}, env={"AQD_SESSION": "planner"})
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "nowhere"))
    m = _mod()
    assert not m.run({"session_id": "s1"}, env={"AQD_SESSION": "planner"})


def test_drain_defaults_on_for_gm_too_when_no_allowlist(tmp_path, monkeypatch):
    d = _data(tmp_path, agent="gm")
    monkeypatch.setenv("ORCHESTRA_DIR", str(d))
    m = _mod()
    out = m.run({"session_id": "s1"}, env={"AQD_SESSION": "gm"})
    assert out and out["decision"] == "block"
