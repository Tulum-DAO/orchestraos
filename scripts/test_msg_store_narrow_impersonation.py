"""Narrow impersonation refusal: an EMPTY body under another agent's name is refused by default."""
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


def _as(monkeypatch, caller):
    monkeypatch.setattr(ms, "sender_identity", lambda: caller)
    monkeypatch.delenv("IMPERSONATION_REFUSE", raising=False)


def _count(db):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    finally:
        conn.close()


def test_canon_seat_strips_both_alias_forms():
    for n in (2, 27):
        assert ms._canon_seat(f"alpha-gen{n}") == "alpha"
        assert ms._canon_seat(f"alpha-g{n}") == "alpha"
    assert ms._canon_seat(" builder ") == "builder"
    assert ms._canon_seat("gateway") == "gateway"
    assert ms._canon_seat(None) == ""


def test_empty_body_under_another_name_is_refused_and_no_row_written(store, monkeypatch):
    s, data, db = store
    _as(monkeypatch, "alice")
    with pytest.raises(ms.ImpersonatedSender):
        s.send("bob", "carol", "message", subject="x", body="")
    assert _count(db) == 0


def test_shadow_line_is_written_before_the_refusal(store, monkeypatch, tmp_path):
    s, data, db = store
    _as(monkeypatch, "alice")
    monkeypatch.setenv("ADDRESSABILITY_LOG_FILE", str(tmp_path / "addressability.log"))
    with pytest.raises(ms.ImpersonatedSender):
        s.send("bob", "carol", "message", subject="x", body=" ")
    log = ms._addressability_log_path().read_text()
    assert "[impersonation-shadow] WOULD REFUSE" in log
    assert "[impersonation] REFUSED" in log
    assert log.index("[impersonation-shadow]") < log.index("[impersonation] REFUSED")


def test_relay_with_real_content_is_still_allowed(store, monkeypatch):
    s, data, db = store
    _as(monkeypatch, "alice")
    mid = s.send("bob", "carol", "message", subject="x", body="a relayed message with content")
    assert s.get(mid)["from_agent"] == "bob"


def test_own_alias_with_empty_body_is_not_impersonation(store, monkeypatch):
    s, data, db = store
    _as(monkeypatch, "bob-gen" + str(4))
    mid = s.send("bob", "carol", "message", subject="x", body="")
    assert s.get(mid) is not None


def test_unknown_caller_fails_open(store, monkeypatch):
    s, data, db = store
    _as(monkeypatch, None)
    mid = s.send("bob", "carol", "message", subject="x", body="")
    assert s.get(mid) is not None


def test_kill_file_disarms_the_narrow_refusal(store, monkeypatch, tmp_path):
    s, data, db = store
    _as(monkeypatch, "alice")
    kill = tmp_path / "ADDRESSABILITY_DISABLED"
    kill.write_text("")
    monkeypatch.setattr(ms, "ADDRESSABILITY_KILL_FILE", kill)
    mid = s.send("bob", "carol", "message", subject="x", body="")
    assert s.get(mid) is not None


def test_narrow_predicate_truth_table():
    f = ms._narrow_impersonation_refusal
    assert f(None, "bob") is False
    assert f({"trivial_body": False, "impersonated": True, "caller": "alice"}, "bob") is False
    assert f({"trivial_body": True, "impersonated": False, "caller": "bob"}, "bob") is False
    assert f({"trivial_body": True, "impersonated": True, "caller": "bob-g2"}, "bob") is False
    assert f({"trivial_body": True, "impersonated": True, "caller": "alice"}, "bob") is True
