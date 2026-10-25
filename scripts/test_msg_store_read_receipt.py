"""Read receipt: only the addressee running `msg_store.py get` acknowledges a row."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import msg_store as ms  # noqa: E402

SCHEMA = """
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
"""


@pytest.fixture
def store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    db = data / "state" / "tasks.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(SCHEMA)
    conn.close()
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    monkeypatch.setenv("MSG_STORE_NO_KICK", "1")
    monkeypatch.delenv("TMUX_PANE", raising=False)
    s = ms.MessageStore(db_path=str(db))
    s.migrate()
    return s, data, db


def _row(db, msg_id, to_agent="bob", from_agent="alice", type="message", status="pending",
         delivered_at=None, metadata=None):
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO messages (id, type, from_agent, to_agent, subject, body, status,"
        " delivered_at, metadata) VALUES (?,?,?,?,?,?,?,?,?)",
        (msg_id, type, from_agent, to_agent, "subject", "a real body", status, delivered_at,
         json.dumps(metadata) if metadata else None))
    conn.commit()
    conn.close()


def _status(db, msg_id):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT status FROM messages WHERE id=?", (msg_id,)).fetchone()[0]
    finally:
        conn.close()


def _cli(data, db, *args):
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    env.update(ORCHESTRA_DIR=str(data), MSG_DB_PATH=str(db), MSG_STORE_NO_KICK="1")
    r = subprocess.run([sys.executable, str(ROOT / "msg_store.py"), *args],
                       capture_output=True, text=True, env=env, timeout=60)
    return r


def test_addressee_read_acknowledges(store):
    s, data, db = store
    _row(db, "m1", to_agent="bob")
    assert s.acknowledge_read("m1", "bob") is True
    assert _status(db, "m1") == "acknowledged"


def test_non_addressee_read_is_read_only(store):
    s, data, db = store
    _row(db, "m1", to_agent="bob")
    assert s.acknowledge_read("m1", "carol") is False
    assert s.acknowledge_read("m1", "") is False
    assert s.acknowledge_read("m1", None) is False
    assert _status(db, "m1") == "pending"


def test_unknown_id_is_false(store):
    s, data, db = store
    assert s.acknowledge_read("nope", "bob") is False


def test_undelivered_held_message_is_not_consumed_by_a_lookup(store):
    s, data, db = store
    _row(db, "h1", to_agent="bob", type="held_message")
    assert s.acknowledge_read("h1", "bob") is False
    assert _status(db, "h1") == "pending"


def test_delivered_held_message_can_be_read(store):
    s, data, db = store
    _row(db, "h2", to_agent="bob", type="held_message", status="delivered",
         delivered_at="2026-10-01T00:00:00")
    assert s.acknowledge_read("h2", "bob") is True
    assert _status(db, "h2") == "acknowledged"


def test_cli_get_with_agent_records_receipt(store):
    s, data, db = store
    _row(db, "m1", to_agent="bob")
    r = _cli(data, db, "get", "--id", "m1", "--agent", "bob")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["status"] == "acknowledged"
    assert out["read_receipt_by"] == "bob"
    assert _status(db, "m1") == "acknowledged"


def test_cli_get_by_observer_does_not_ack(store):
    s, data, db = store
    _row(db, "m1", to_agent="bob")
    r = _cli(data, db, "get", "--id", "m1", "--agent", "carol")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["status"] == "pending"
    assert _status(db, "m1") == "pending"


def test_cli_get_without_identity_is_read_only(store):
    s, data, db = store
    _row(db, "m1", to_agent="bob")
    r = _cli(data, db, "get", "--message-id", "m1")
    assert r.returncode == 0, r.stderr
    assert _status(db, "m1") == "pending"
