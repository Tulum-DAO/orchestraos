"""One message tenant from config, and readers that never silently hide mail under an unknown tenant.

Defect (2026-11-27): msg_store.py wrote and filtered the literal "operator" while the operator's
other readers filtered "shaw"; tenant-filtered readers (the Grok mail watcher, `inbox`, Arturo) hid
every row of the other tenant. Seven messages were stranded, one for 36 hours.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import msg_store as ms  # noqa: E402
from orchestra_cli import settings as st  # noqa: E402


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
def install(tmp_path, monkeypatch):
    """A scratch install: data dir with an empty bus, a config whose message tenant is 'shaw',
    and a tenant registry that knows 'shaw' and a second real tenant 'kai'."""
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    db = data / "state" / "tasks.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(SCHEMA)
    conn.close()
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text('[operator]\nid = "operator"\n\n[messages]\ntenant = "shaw"\n')
    (data / "state" / "tenants.json").write_text(json.dumps({"shaw": {"role": "admin"},
                                                             "kai": {"role": "client"}}))
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(cfg))
    monkeypatch.setenv("MSG_DB_PATH", str(db))
    monkeypatch.setenv("MSG_STORE_NO_KICK", "1")
    monkeypatch.delenv("ORCHESTRA_TENANT", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    s = ms.MessageStore(db_path=str(db))
    s.migrate()
    return s, data, db, cfg


def _rows(db):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM messages ORDER BY created_at")]
    finally:
        conn.close()


def _insert(db, msg_id, to_agent, tenant):
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO messages (id, type, from_agent, to_agent, subject, body, status, tenant_id)"
                 " VALUES (?,?,?,?,?,?,?,?)", (msg_id, "task", "alice", to_agent, "s", "b", "pending", tenant))
    conn.commit()
    conn.close()


# --- resolution -----------------------------------------------------------------------------------

def test_messages_tenant_beats_operator_id(install):
    assert st.load_settings().message_tenant == "shaw"


def test_operator_id_is_the_fallback(install):
    _, _, _, cfg = install
    cfg.write_text('[operator]\nid = "dana"\n')
    assert st.load_settings().message_tenant == "dana"


def test_env_beats_config(install, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_TENANT", "env-tenant")
    assert st.load_settings().message_tenant == "env-tenant"


def test_no_config_is_operator(install, monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(tmp_path / "absent.toml"))
    assert st.load_settings().message_tenant == "operator"


# --- the banner send path writes the configured tenant ---------------------------------------------

def test_banner_path_send_writes_configured_tenant(install):
    """Exactly what a wake banner prints: `python3 <root>/msg_store.py send ...`, as a subprocess."""
    _, _, db, _ = install
    env = dict(os.environ)
    r = subprocess.run([sys.executable, str(ROOT / "msg_store.py"), "send", "--from", "alice",
                        "--to", "grok-gm", "--type", "task", "--subject", "s", "--body", "b"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    rows = _rows(db)
    assert [x["tenant_id"] for x in rows] == ["shaw"]


def test_banner_send_is_visible_to_a_strict_shaw_reader(install):
    """The old tree's readers (and the live Grok watcher) filter tenant_id='shaw' exactly."""
    s, _, db, _ = install
    s.send(from_agent="alice", to_agent="grok-gm", type="task", subject="s", body="b")
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT count(*) FROM messages WHERE to_agent='grok-gm' AND status='pending'"
                     " AND tenant_id='shaw'").fetchone()[0]
    conn.close()
    assert n == 1


def test_cli_tenant_flag_pins_the_tenant(install):
    _, _, db, _ = install
    r = subprocess.run([sys.executable, str(ROOT / "msg_store.py"), "send", "--from", "alice", "--to", "bob",
                        "--type", "task", "--subject", "s", "--body", "b", "--tenant", "kai"],
                       capture_output=True, text=True, env=dict(os.environ), timeout=60)
    assert r.returncode == 0, r.stderr
    assert [x["tenant_id"] for x in _rows(db)] == ["kai"]


def test_reply_without_parent_tenant_uses_configured(install):
    s, _, db, _ = install
    _insert(db, "msg_parent", "bob", None)
    s.reply("msg_parent", body="ok")
    assert sorted(x["tenant_id"] or "" for x in _rows(db)) == ["", "shaw"]


# --- the reader guard ------------------------------------------------------------------------------

def test_inbox_surfaces_unknown_tenant_mail_flagged(install, capsys):
    s, _, db, _ = install
    _insert(db, "msg_legacy", "grok-gm", "operator")       # 'operator' is in no registry here
    _insert(db, "msg_own", "grok-gm", "shaw")
    got = {m["id"]: m for m in s.inbox("grok-gm")}
    assert set(got) == {"msg_legacy", "msg_own"}
    assert got["msg_legacy"].get("tenant_mismatch") is True
    assert not got["msg_own"].get("tenant_mismatch")
    assert "operator" in capsys.readouterr().err


def test_inbox_still_hides_another_known_tenant(install):
    s, _, db, _ = install
    _insert(db, "msg_kai", "grok-gm", "kai")
    assert [m["id"] for m in s.inbox("grok-gm")] == []


def test_explicit_tenant_is_strict(install):
    s, _, db, _ = install
    _insert(db, "msg_legacy", "bob", "operator")
    _insert(db, "msg_own", "bob", "shaw")
    assert [m["id"] for m in s.inbox("bob", tenant_id="shaw")] == ["msg_own"]


def test_cli_inbox_sees_configured_tenant_mail(install):
    """The banner's `inbox` hid every 'shaw' row (it filtered the literal 'operator')."""
    _, _, db, _ = install
    _insert(db, "msg_legacy", "chatgpt", "shaw")
    r = subprocess.run([sys.executable, str(ROOT / "msg_store.py"), "inbox", "--agent", "chatgpt"],
                       capture_output=True, text=True, env=dict(os.environ), timeout=60)
    assert r.returncode == 0, r.stderr
    assert [m["id"] for m in json.loads(r.stdout)["messages"]] == ["msg_legacy"]


def test_held_pending_and_queued_open_use_the_guard(install):
    s, _, db, _ = install
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO messages (id, type, from_agent, to_agent, subject, body, status, tenant_id, metadata)"
                 " VALUES ('msg_h', 'held_message', 'alice', 'sess', 's', 'b', 'pending', 'legacy-tenant', NULL)")
    conn.commit()
    conn.close()
    assert [m["id"] for m in s.held_pending_for("sess")] == ["msg_h"]


def test_no_registry_means_only_the_configured_tenant_is_known(install):
    s, data, db, _ = install
    (data / "state" / "tenants.json").unlink()
    _insert(db, "msg_kai", "bob", "kai")                    # unknown now -> surfaced
    assert [m["id"] for m in s.inbox("bob")] == ["msg_kai"]


# --- the other readers/writers ---------------------------------------------------------------------

def test_grok_watcher_sees_configured_and_unknown_tenants(install):
    sys.path.insert(0, str(ROOT / "scripts"))
    import grok_mail_watch as gw
    _, _, db, _ = install
    _insert(db, "msg_a", "grok-gm", "shaw")
    _insert(db, "msg_b", "grok-gm", "operator")
    _insert(db, "msg_c", "grok-gm", "kai")
    assert sorted(gw.pending_ids(str(db), "grok-gm", None)) == ["msg_a", "msg_b"]


def test_arturo_proxy_has_no_tenant_literal():
    """Mail calls (send/inbox) resolve the install's tenant; the agent-hierarchy table is not mail."""
    src = (ROOT / "services" / "arturo" / "arturo-proxy.py").read_text().splitlines()
    hits = [ln for ln in src if re.search(r"tenant_id\s*=\s*[\"']operator[\"']", ln) and "hierarchy" not in ln]
    assert hits == []


def test_msg_store_has_no_operator_tenant_default_for_mail():
    src = (ROOT / "msg_store.py").read_text()
    for fn in ("def send(", "def inbox(", "def held_pending_for(", "def disposition_stats(", "def queued_open("):
        i = src.index(fn)
        sig = src[i:src.index(")", src.index("tenant_id", i)) + 1]
        assert '"operator"' not in sig, fn


# --- one-shot close of hand re-delivered originals ---------------------------------------------------

def _migrate(db, *args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "msg_tenant_migrate.py"), "--db", str(db), *args],
                          capture_output=True, text=True, timeout=60)


def test_migrate_dry_run_writes_nothing(install):
    _, _, db, _ = install
    _insert(db, "msg_old", "chatgpt", "operator")
    r = _migrate(db, "--close", "msg_old=msg_new")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["action"] == "would close"
    assert _rows(db)[0]["status"] == "pending"


def test_migrate_apply_closes_only_pending_named_rows(install):
    _, _, db, _ = install
    _insert(db, "msg_old", "chatgpt", "operator")
    _insert(db, "msg_done", "chatgpt", "operator")
    _insert(db, "msg_other", "chatgpt", "operator")
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE messages SET status='acknowledged' WHERE id='msg_done'")
    conn.commit()
    conn.close()
    r = _migrate(db, "--close", "msg_old=msg_new", "--close", "msg_done=msg_x", "--close", "msg_gone=msg_y", "--apply")
    assert r.returncode == 0, r.stderr
    acts = {d["id"]: d["action"] for d in map(json.loads, r.stdout.splitlines())}
    assert acts == {"msg_old": "close", "msg_done": "skip", "msg_gone": "skip"}
    rows = {x["id"]: x for x in _rows(db)}
    assert rows["msg_old"]["status"] == "retracted"
    assert "re-delivered as msg_new" in rows["msg_old"]["error"]
    assert rows["msg_done"]["status"] == "acknowledged"
    assert rows["msg_other"]["status"] == "pending"


def test_migrate_refuses_a_malformed_pair(install):
    _, _, db, _ = install
    r = _migrate(db, "--close", "msg_old")
    assert r.returncode != 0
