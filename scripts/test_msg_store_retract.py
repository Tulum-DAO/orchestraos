"""Sender-side retract: a pending row you sent is withdrawn; anything else is refused."""
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


def test_sender_retracts_pending_row(store):
    s, data, db = store
    _row(db, "m1", from_agent="alice", to_agent="bob")
    assert s.retract("m1", sender="alice", reason="sent to the wrong agent") is True
    assert _status(db, "m1") == "retracted"
    meta = json.loads(s.get("m1")["metadata"])
    assert meta["retracted_by"] == "alice" and meta["retract_reason"]


def test_retracted_row_cannot_be_claimed_by_router(store):
    s, data, db = store
    _row(db, "m1", from_agent="alice")
    s.retract("m1", sender="alice", reason="obsolete")
    assert s.claim("m1") is False
    assert not [m for m in s.inbox("bob") if m["id"] == "m1"]


def test_other_agent_cannot_retract(store):
    s, data, db = store
    _row(db, "m1", from_agent="alice")
    assert s.retract("m1", sender="carol", reason="x") is False
    assert _status(db, "m1") == "pending"
    assert "only the sender" in s.retract_refusal("m1", "carol")


def test_delivered_row_cannot_be_retracted(store):
    s, data, db = store
    _row(db, "m1", from_agent="alice")
    s.deliver("m1")
    assert s.retract("m1", sender="alice", reason="too late") is False
    assert "ALREADY DELIVERED" in s.retract_refusal("m1", "alice")


def test_unknown_id_and_missing_reason(store):
    s, data, db = store
    assert s.retract("nope", sender="alice", reason="x") is False
    assert s.retract_refusal("nope", "alice") == "no such message"
    _row(db, "m1", from_agent="alice")
    with pytest.raises(ValueError):
        s.retract("m1", sender="alice", reason="  ")


def test_dispose_declined_does_not_stop_delivery(store):
    s, data, db = store
    _row(db, "m1", from_agent="alice")
    assert s.dispose("m1", "declined", by="bob", reason="not mine") is True
    assert _status(db, "m1") == "pending"


def test_cli_retract_success_and_refusal_exit_codes(store):
    s, data, db = store
    _row(db, "m1", from_agent="alice")
    _row(db, "m2", from_agent="alice")
    r = _cli(data, db, "retract", "--id", "m1", "--from", "alice", "--reason", "dup")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["retracted"] is True
    r = _cli(data, db, "retract", "--message-id", "m2", "--from", "carol", "--reason", "x")
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["retracted"] is False and "only the sender" in out["error"]
    assert _status(db, "m2") == "pending"
