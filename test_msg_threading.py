"""
test_msg_threading.py — TDD tests for msg_store threading features.

Tests (written FIRST, must fail before implementation):
1. test_reply_stamps_parent_id
2. test_send_closes_thread_sets_metadata
3. test_send_closes_thread_merges_existing_metadata
4. test_send_default_no_closes_thread
5. test_reply_close_sets_closes_thread_metadata
"""

import json
import sqlite3
import pytest
from pathlib import Path

from msg_store import MessageStore


@pytest.fixture
def store(tmp_path):
    """Create a fresh MessageStore backed by a tmp SQLite db."""
    db_file = tmp_path / "test_tasks.db"

    # Bootstrap the two base tables the codebase expects
    conn = sqlite3.connect(str(db_file))
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

    ms = MessageStore(db_path=str(db_file))
    ms.migrate()
    return ms


# ── Test 1: reply() always stamps parent_id ────────────────────

def test_reply_stamps_parent_id(store):
    """reply() must set parent_id = the original message id unconditionally."""
    msg_id = store.send(
        from_agent="agent-a", to_agent="agent-b",
        type="task_request", subject="Test", body="Hello"
    )
    reply_id = store.reply(msg_id, body="ok")
    reply = store.get(reply_id)
    assert reply is not None, "Reply message not found"
    assert reply["parent_id"] == msg_id, (
        f"Expected parent_id={msg_id!r}, got {reply['parent_id']!r}"
    )


# ── Test 2: send(closes_thread=True) sets metadata ────────────

def test_send_closes_thread_sets_metadata(store):
    """send(..., closes_thread=True) must write {'closes_thread': true} into metadata column."""
    mid = store.send(
        from_agent="agent-a", to_agent="agent-b",
        type="reply", subject="Done", body="Finished",
        closes_thread=True
    )
    m = store.get(mid)
    assert m is not None
    assert m["metadata"] is not None, "metadata column should not be None when closes_thread=True"
    parsed = json.loads(m["metadata"])
    assert parsed.get("closes_thread") is True, (
        f"Expected closes_thread=True in metadata, got: {parsed}"
    )


# ── Test 3: closes_thread merges with existing metadata ────────

def test_send_closes_thread_merges_existing_metadata(store):
    """send(..., metadata={...}, closes_thread=True) must retain existing keys AND add closes_thread."""
    mid = store.send(
        from_agent="agent-a", to_agent="agent-b",
        type="reply", subject="Done", body="Finished",
        metadata={"foo": "bar"},
        closes_thread=True
    )
    m = store.get(mid)
    assert m is not None
    parsed = json.loads(m["metadata"])
    assert parsed.get("foo") == "bar", f"Existing key 'foo' was lost: {parsed}"
    assert parsed.get("closes_thread") is True, f"closes_thread not merged: {parsed}"


# ── Test 4: default send() leaves metadata unchanged ──────────

def test_send_default_no_closes_thread(store):
    """A plain send() with no metadata and closes_thread defaulting to False → metadata is None."""
    mid = store.send(
        from_agent="agent-a", to_agent="agent-b",
        type="task_request", subject="Plain", body="No metadata"
    )
    m = store.get(mid)
    assert m is not None
    assert m["metadata"] is None, (
        f"Expected metadata=None for plain send, got: {m['metadata']!r}"
    )


# ── Test 5: reply(close=True) sets closes_thread in metadata ──

def test_reply_close_sets_closes_thread_metadata(store):
    """reply(..., close=True) must write {'closes_thread': true} into the reply row's metadata."""
    msg_id = store.send(
        from_agent="agent-a", to_agent="agent-b",
        type="task_request", subject="Work", body="Do this"
    )
    rid = store.reply(msg_id, body="done", close=True)
    reply = store.get(rid)
    assert reply is not None
    assert reply["metadata"] is not None, (
        "metadata should not be None when reply(close=True)"
    )
    parsed = json.loads(reply["metadata"])
    assert parsed.get("closes_thread") is True, (
        f"Expected closes_thread=True in reply metadata, got: {parsed}"
    )
