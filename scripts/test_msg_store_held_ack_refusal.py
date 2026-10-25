"""An undelivered held_message cannot be acked; it stays deliverable."""
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


def test_ack_of_undelivered_held_message_raises_and_row_stays_pending(store):
    s, data, db = store
    _row(db, "h1", type="held_message")
    with pytest.raises(ms.HeldAckRefused):
        s.acknowledge("h1")
    assert _status(db, "h1") == "pending"


def test_router_ack_after_delivery_still_works(store):
    s, data, db = store
    _row(db, "h1", type="held_message")
    s.deliver("h1")
    assert s.acknowledge("h1") is True
    assert _status(db, "h1") == "acknowledged"


def test_held_row_with_delivered_at_can_be_acked(store):
    s, data, db = store
    _row(db, "h2", type="held_message", delivered_at="2026-10-01T00:00:00")
    assert s.acknowledge("h2") is True


def test_ordinary_pending_row_ack_unchanged(store):
    s, data, db = store
    _row(db, "m1")
    assert s.acknowledge("m1") is True
    assert _status(db, "m1") == "acknowledged"


def test_unknown_id_still_false(store):
    s, data, db = store
    assert s.acknowledge("missing") is False


def test_cli_ack_refusal_exits_nonzero(store):
    s, data, db = store
    _row(db, "h1", type="held_message")
    r = _cli(data, db, "ack", "--id", "h1")
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["acknowledged"] is False and "held_message" in out["error"]
    assert _status(db, "h1") == "pending"


def test_cli_ack_ordinary_row_exits_zero(store):
    s, data, db = store
    _row(db, "m1")
    r = _cli(data, db, "ack", "--message-id", "m1")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["acknowledged"] is True
