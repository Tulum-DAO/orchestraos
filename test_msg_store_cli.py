"""
test_msg_store_cli.py — Tests for CLI flag aliases across msg_store.py subcommands
and the queue-drain Stop hook copy-pasteable command digest (Issue #99).
"""

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
import pytest

from msg_store import MessageStore

HERE = Path(__file__).resolve().parent


@pytest.fixture
def store_env(tmp_path, monkeypatch):
    """Create a fresh isolated environment with tasks.db and registry.json."""
    data_dir = tmp_path / "orchestra"
    state_dir = data_dir / "state"
    state_dir.mkdir(parents=True)

    db_path = state_dir / "tasks.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, conversation_id TEXT, task_id TEXT, parent_id TEXT,
            type TEXT NOT NULL, from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
            subject TEXT, body TEXT, priority TEXT NOT NULL DEFAULT 'medium',
            source TEXT DEFAULT 'system', status TEXT NOT NULL DEFAULT 'pending',
            retry_count INTEGER NOT NULL DEFAULT 0, max_retries INTEGER NOT NULL DEFAULT 5,
            metadata TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
            attempted_at TEXT, delivered_at TEXT, acknowledged_at TEXT, archived_at TEXT, error TEXT
        );
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, subject TEXT, participants TEXT, task_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.close()

    monkeypatch.setenv("ORCHESTRA_DIR", str(data_dir))
    ms = MessageStore(db_path=str(db_path))
    ms.migrate()
    return ms, data_dir, db_path


def _run_cli(*args, data_dir=None):
    """Run msg_store.py as a CLI subprocess."""
    import os
    env = dict(os.environ)
    if data_dir:
        env["ORCHESTRA_DIR"] = str(data_dir)
    res = subprocess.run(
        [sys.executable, str(HERE / "msg_store.py"), *args],
        capture_output=True, text=True, env=env
    )
    return res


def test_get_supports_both_id_and_message_id(store_env):
    store, data_dir, _ = store_env
    mid = store.send(from_agent="gm", to_agent="dev", type="task_request", subject="Test get", body="Hello get")

    # via --id
    res_id = _run_cli("get", "--id", mid, data_dir=data_dir)
    assert res_id.returncode == 0, res_id.stderr
    out_id = json.loads(res_id.stdout)
    assert out_id["id"] == mid
    assert out_id["subject"] == "Test get"

    # via --message-id alias
    res_alias = _run_cli("get", "--message-id", mid, data_dir=data_dir)
    assert res_alias.returncode == 0, res_alias.stderr
    out_alias = json.loads(res_alias.stdout)
    assert out_alias["id"] == mid
    assert out_alias["subject"] == "Test get"


def test_ack_supports_both_id_and_message_id(store_env):
    store, data_dir, _ = store_env
    mid1 = store.send(from_agent="gm", to_agent="dev", type="task_request", subject="Test ack 1", body="Hello ack 1")
    mid2 = store.send(from_agent="gm", to_agent="dev", type="task_request", subject="Test ack 2", body="Hello ack 2")

    # ack via --id
    res_id = _run_cli("ack", "--id", mid1, data_dir=data_dir)
    assert res_id.returncode == 0, res_id.stderr
    out_id = json.loads(res_id.stdout)
    assert out_id["acknowledged"] is True
    assert out_id["id"] == mid1

    # verify in store
    assert store.get(mid1)["status"] == "acknowledged"

    # ack via --message-id
    res_alias = _run_cli("ack", "--message-id", mid2, data_dir=data_dir)
    assert res_alias.returncode == 0, res_alias.stderr
    out_alias = json.loads(res_alias.stdout)
    assert out_alias["acknowledged"] is True
    assert out_alias["id"] == mid2

    # verify in store
    assert store.get(mid2)["status"] == "acknowledged"


def test_reply_supports_both_id_and_message_id(store_env):
    store, data_dir, _ = store_env
    mid1 = store.send(from_agent="gm", to_agent="dev", type="task_request", subject="Test reply 1", body="Hello reply 1")
    mid2 = store.send(from_agent="gm", to_agent="dev", type="task_request", subject="Test reply 2", body="Hello reply 2")

    # reply via --id
    res_id = _run_cli("reply", "--id", mid1, "--body", "reply 1 ok", data_dir=data_dir)
    assert res_id.returncode == 0, res_id.stderr
    out_id = json.loads(res_id.stdout)
    assert out_id["replied"] is True

    # reply via --message-id
    res_alias = _run_cli("reply", "--message-id", mid2, "--body", "reply 2 ok", data_dir=data_dir)
    assert res_alias.returncode == 0, res_alias.stderr
    out_alias = json.loads(res_alias.stdout)
    assert out_alias["replied"] is True


def test_dispose_supports_both_id_and_message_id(store_env):
    store, data_dir, _ = store_env
    mid1 = store.send(from_agent="gm", to_agent="dev", type="task_request", subject="Test dispose 1", body="Hello dispose 1")
    mid2 = store.send(from_agent="gm", to_agent="dev", type="task_request", subject="Test dispose 2", body="Hello dispose 2")

    # dispose via --id
    res_id = _run_cli("dispose", "--id", mid1, "--disposition", "acted", "--by", "dev", data_dir=data_dir)
    assert res_id.returncode == 0, res_id.stderr
    out_id = json.loads(res_id.stdout)
    assert out_id["disposed"] is True
    assert out_id["id"] == mid1

    # dispose via --message-id
    res_alias = _run_cli("dispose", "--message-id", mid2, "--disposition", "declined", "--by", "dev", "--reason", "not applicable", data_dir=data_dir)
    assert res_alias.returncode == 0, res_alias.stderr
    out_alias = json.loads(res_alias.stdout)
    assert out_alias["disposed"] is True
    assert out_alias["id"] == mid2


def test_queue_drain_digest_shows_copypasteable_commands(tmp_path, monkeypatch):
    """Verify queue-drain digest prints exact copy-pasteable get and ack commands on digest line."""
    import importlib.util

    data_dir = tmp_path / "data"
    state_dir = data_dir / "state"
    state_dir.mkdir(parents=True)
    (data_dir / "registry.json").write_text(json.dumps({
        "agents": {"planner": {"name": "planner", "tmux_session": "planner"}}
    }))

    db = state_dir / "tasks.db"
    conn = sqlite3.connect(db)
    conn.execute("create table messages (id text primary key, from_agent text, to_agent text, type text, subject text, body text, status text, created_at text, archived_at text, metadata text)")
    conn.execute("create table conversations (id text primary key)")
    ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 120))
    conn.execute("insert into messages values (?,?,?,?,?,?,?,?,?,?)",
                 ("msg_42abc", "gm", "planner", "task_request", "Check the server", "body", "pending", ts, None, "{}"))
    conn.commit()
    conn.close()

    monkeypatch.setenv("ORCHESTRA_DIR", str(data_dir))

    drain_path = HERE / "hooks" / "agent-queue-drain.py"
    spec = importlib.util.spec_from_file_location("agent_queue_drain", drain_path)
    drain_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(drain_mod)

    out = drain_mod.run({"session_id": "s1", "stop_hook_active": False}, env={"AQD_SESSION": "planner"})
    assert out is not None
    assert out["decision"] == "block"
    reason = out["reason"]

    assert "[QUEUE-DIGEST]" in reason
    assert "msg_42abc" in reason
    # Acceptance criteria: copy-pasteable get/ack pair on digest line
    assert "python3 msg_store.py get --id msg_42abc" in reason
    assert "python3 msg_store.py ack --id msg_42abc" in reason
