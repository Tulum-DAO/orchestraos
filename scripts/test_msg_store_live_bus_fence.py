"""A test can never open a message bus inside a real data dir."""
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
def real_dir(tmp_path, monkeypatch):
    real = tmp_path / "real-install"
    (real / "state").mkdir(parents=True)
    monkeypatch.setenv(ms._ocs.PROTECTED_ENV, str(real))
    return real


def test_explicit_path_inside_a_real_data_dir_is_refused(real_dir):
    with pytest.raises(ms.LiveBusRefused, match="LIVE message bus"):
        ms.MessageStore(db_path=str(real_dir / "state" / "tasks.db"))
    assert not (real_dir / "state" / "tasks.db").exists()


def test_default_constructor_resolving_to_a_real_dir_is_refused(real_dir, monkeypatch):
    monkeypatch.delenv("MSG_DB_PATH", raising=False)
    monkeypatch.setattr(ms, "DB_PATH", real_dir / "state" / "tasks.db")
    with pytest.raises(ms.LiveBusRefused):
        ms.MessageStore()


def test_relative_route_into_the_real_dir_is_refused(real_dir):
    sneaky = str(real_dir / "state" / ".." / "state" / "tasks.db")
    with pytest.raises(ms.LiveBusRefused):
        ms.MessageStore(db_path=sneaky)


def test_symlink_into_the_real_dir_is_refused(real_dir, tmp_path):
    link = tmp_path / "innocent"
    link.symlink_to(real_dir / "state")
    with pytest.raises(ms.LiveBusRefused):
        ms.MessageStore(db_path=str(link / "tasks.db"))


def test_is_a_real_data_dir_write_error(real_dir):
    from orchestra_cli.settings import RealDataDirWrite
    with pytest.raises(RealDataDirWrite):
        ms.MessageStore(db_path=str(real_dir / "state" / "tasks.db"))


def test_allow_live_is_the_explicit_escape(real_dir):
    s = ms.MessageStore(db_path=str(real_dir / "state" / "tasks.db"), allow_live=True)
    assert s.db_path.endswith("tasks.db")


def test_scratch_db_is_allowed_so_the_fence_does_not_refuse_everything(store, real_dir):
    s, data, db = store
    _row(db, "m1")
    assert s.get("m1")["to_agent"] == "bob"
    assert ms.MessageStore(db_path=":memory:").db_path == ":memory:"


def test_fence_is_inert_outside_pytest(real_dir, monkeypatch):
    monkeypatch.setattr(ms, "_under_pytest", lambda: False)
    ms.MessageStore(db_path=str(real_dir / "state" / "tasks.db"))   # must NOT raise
