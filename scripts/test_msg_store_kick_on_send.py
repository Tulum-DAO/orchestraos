"""Kick-on-send: a send to the installed bus starts a router pass; nothing else ever does."""
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


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A fake configured install whose code root is this checkout. Nothing is spawned:
    subprocess.Popen is replaced and every call is recorded."""
    data = tmp_path / "install-data"
    (data / "state").mkdir(parents=True)
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text("")
    calls = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            calls.append((cmd, kw))

    import subprocess
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(cfg))
    monkeypatch.delenv("ORCHESTRA_ROOT", raising=False)
    monkeypatch.setattr(ms, "_data_dir", lambda include_env=True: data)
    monkeypatch.setattr(ms, "_kick_suppressed", lambda: False)
    return data, calls


def test_send_to_the_installed_bus_kicks_the_router_once(install):
    data, calls = install
    ms.kick_router_after_send(str(data / "state" / "tasks.db"))
    assert len(calls) == 1
    cmd, kw = calls[0]
    assert cmd[-2:] == [str(ROOT / "scripts" / "message-router.py"), "--cron"]
    assert kw["env"]["ORCHESTRA_DIR"] == str(data)
    assert "MSG_DB_PATH" not in kw["env"]
    assert kw["start_new_session"] is True and kw["cwd"] == str(ROOT)


def test_scratch_store_never_kicks(install, tmp_path):
    data, calls = install
    ms.kick_router_after_send(str(tmp_path / "scratch" / "tasks.db"))
    assert calls == []


def test_other_code_root_never_kicks(install, monkeypatch, tmp_path):
    data, calls = install
    monkeypatch.setenv("ORCHESTRA_ROOT", str(tmp_path))
    ms.kick_router_after_send(str(data / "state" / "tasks.db"))
    assert calls == []


def test_unconfigured_checkout_never_kicks(install, monkeypatch, tmp_path):
    data, calls = install
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(tmp_path / "missing.toml"))
    ms.kick_router_after_send(str(data / "state" / "tasks.db"))
    assert calls == []


def test_router_disabled_in_config_never_kicks(install, tmp_path):
    data, calls = install
    (tmp_path / "orchestra.toml").write_text("[router]\nenabled = false\n")
    ms.kick_router_after_send(str(data / "state" / "tasks.db"))
    assert calls == []


def test_suppressed_under_pytest_and_by_env(monkeypatch):
    monkeypatch.delenv("MSG_STORE_NO_KICK", raising=False)
    assert ms._kick_suppressed() is True          # we are under pytest
    monkeypatch.setenv("MSG_STORE_NO_KICK", "1")
    assert ms._kick_suppressed() is True


def test_spawn_failure_never_breaks_a_send(store, monkeypatch):
    s, data, db = store
    monkeypatch.setattr(ms, "_kick_suppressed", lambda: False)

    def boom(_db):
        raise OSError("no router")
    monkeypatch.setattr(ms, "_kick_target", boom)
    mid = s.send("alice", "bob", "message", subject="hi", body="a real body")
    assert s.get(mid)["status"] == "pending"


def test_send_calls_the_kick_with_its_own_db(store, monkeypatch):
    s, data, db = store
    seen = []
    monkeypatch.setattr(ms, "kick_router_after_send", lambda p: seen.append(p))
    s.send("alice", "bob", "message", subject="hi", body="a real body")
    assert seen == [str(db)]
