"""Live sends for tasks and dispatches (design §6), proven against the fenced scratch stores with the
real CLIs: a human task is a feature=commitment card keyed by the commitment id, a chase task
carries the approved chase date, and a dispatch is one message whose subject carries its id."""
import json
import os
import sqlite3
import sys

from postcall import effects
from postcall.effects import Effect

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
APPROVAL_CLI = os.path.join(REPO, "scripts", "approval.py")
MSG_CLI = os.path.join(REPO, "msg_store.py")

ITEM = {"owner": "shaw", "text": "Send Dana the pricing deck", "counterpart": "Dana Reyes",
        "due_ts": "2026-11-27T17:00:00-05:00", "due_source": "by Friday",
        "quote": "I'll send you the pricing deck by Friday.", "citation": "pocket:r1@02:10",
        "call_title": "Synthetic Vendor Sync"}


def _live(request, monkeypatch, *clis):
    request.node.postcall_allow_argv = [[sys.executable, c] for c in clis]
    monkeypatch.setenv("POSTCALL_APPROVAL_CLI", APPROVAL_CLI)
    monkeypatch.setenv("POSTCALL_MSG_CLI", MSG_CLI)
    # The scratch DB gets the human-task columns the live DB has. Armed by env, never by the
    # operator's home sentinel, so CI (no sentinel) and this box run the same schema.
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "m20260825_human_task")
    return effects.adapter_for("live")


def _scratch_bus(path):
    """The base tables the message bus expects (as test_msg_store_cli.py builds them), then migrate()."""
    with sqlite3.connect(path) as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, conversation_id TEXT, task_id TEXT, parent_id TEXT,
                type TEXT NOT NULL, from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
                subject TEXT, body TEXT, priority TEXT NOT NULL DEFAULT 'medium',
                source TEXT DEFAULT 'system', status TEXT NOT NULL DEFAULT 'pending',
                retry_count INTEGER NOT NULL DEFAULT 0, max_retries INTEGER NOT NULL DEFAULT 5,
                metadata TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
                attempted_at TEXT, delivered_at TEXT, acknowledged_at TEXT, archived_at TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, subject TEXT, participants TEXT, task_id TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')));""")
    sys.path.insert(0, REPO)
    from msg_store import MessageStore
    MessageStore(db_path=path).migrate()


def _approval_row(rid):
    with sqlite3.connect(os.environ["APPROVAL_DB_PATH"]) as c:
        c.row_factory = sqlite3.Row
        return dict(c.execute("SELECT * FROM approval_requests WHERE id=?", (rid,)).fetchone())


def test_take_files_one_commitment_human_task_without_a_push(request, monkeypatch):
    live = _live(request, monkeypatch, APPROVAL_CLI)
    eff = Effect("task:cmt_aaaaaaaaaaaa", "task", {"cmt": "cmt_aaaaaaaaaaaa", "key": "src_1", "action": "take",
                                                   "decision_src": "card:x#2", "item": ITEM})
    assert live.find(eff) is None
    rid = live.send(eff)
    assert live.find(eff) == rid
    row = _approval_row(rid)
    assert (row["from_agent"], row["kind"], row["feature"], row["op_key"]) == (
        "postcall-dev", "human_task", "commitment", "cmt_aaaaaaaaaaaa")
    assert json.loads(row["evidence"]) == {"due_ts": "2026-11-27T17:00:00-05:00", "due_source": "by Friday"}
    assert "Send Dana the pricing deck" in row["block_task"]
    assert '"I\'ll send you the pricing deck by Friday."' in row["summary"]
    assert row["notified_at"] is None


def test_track_files_a_chase_task_due_on_the_approved_chase_date(request, monkeypatch):
    live = _live(request, monkeypatch, APPROVAL_CLI)
    their = dict(ITEM, owner="participant", text="Dana shares the API credentials", due_ts=None,
                 due_source="none stated")
    eff = Effect("task:cmt_bbbbbbbbbbbb", "task", {"cmt": "cmt_bbbbbbbbbbbb", "key": "src_2", "action": "track",
                                                   "chase_date": "2026-11-26T10:00:00-05:00",
                                                   "decision_src": "card:x#3", "item": their})
    row = _approval_row(live.send(eff))
    assert row["block_task"].startswith("Chase Dana Reyes: Dana shares the API credentials")
    assert json.loads(row["evidence"]) == {"due_ts": "2026-11-26T10:00:00-05:00",
                                           "due_source": "chase default: call + 2bd"}


def test_dispatch_is_one_tagged_message_to_the_frozen_seat(request, monkeypatch):
    _scratch_bus(os.environ["MSG_DB_PATH"])
    live = _live(request, monkeypatch, MSG_CLI)
    eff = Effect("dispatch:cmt_cccccccccccc", "dispatch", {
        "cmt": "cmt_cccccccccccc", "key": "src_3", "seat": "pm-example", "agent_work": "draft the pricing deck",
        "decision_src": "card:qnr_1#2", "item": ITEM})
    assert live.find(eff) is None
    mid = live.send(eff)
    assert live.find(eff) == mid
    with sqlite3.connect(os.environ["MSG_DB_PATH"]) as c:
        to, subject, body, prio = c.execute("SELECT to_agent, subject, body, priority FROM messages WHERE id=?",
                                            (mid,)).fetchone()
    assert to == "pm-example" and prio == "high"
    assert subject == "[postcall dispatch:cmt_cccccccccccc] draft the pricing deck"
    assert "Approved by Shaw on card card:qnr_1#2" in body
    assert "never send anything as Shaw" in body


def test_results_notice_is_one_tagged_message_to_gm(request, monkeypatch):
    _scratch_bus(os.environ["MSG_DB_PATH"])
    live = _live(request, monkeypatch, MSG_CLI)
    eff = Effect("results:card:pocket:r1:r0:p1", "results", {
        "to": "gm", "subject": "Post-call results ready: Synthetic Vendor Sync", "body": "1/1 done, unreviewed 0"})
    assert live.find(eff) is None
    mid = live.send(eff)
    assert live.find(eff) == mid
    with sqlite3.connect(os.environ["MSG_DB_PATH"]) as c:
        to, subject, body = c.execute("SELECT to_agent, subject, body FROM messages WHERE id=?", (mid,)).fetchone()
    assert to == "gm"
    assert subject == "[postcall results:card:pocket:r1:r0:p1] Post-call results ready: Synthetic Vendor Sync"
    assert body == "1/1 done, unreviewed 0"
