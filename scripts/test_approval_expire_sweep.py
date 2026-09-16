"""RED-first (gm msg_86bcc168 item 4): approval_requests rows past expires_at keep status
'pending' forever (ApprovalStore.expire_due has NO caller) and the author is never told.
expire_due grows a dry-run + per-row author notice; approval.py gets an `expire-sweep`
command that is dry-run by default (--apply to write). The EXPIRE_PENDING the operator gate stays."""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from approval_schema import ApprovalStore  # noqa: E402
import approval_schema  # noqa: E402


@pytest.fixture
def store(tmp_path):
    s = ApprovalStore(db_path=str(tmp_path / "t.db"))
    s.migrate()
    return s


def _backdate(store, rid, hours):
    exp = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with sqlite3.connect(store.db_path) as c:
        c.execute("UPDATE approval_requests SET expires_at=? WHERE id=?", [exp, rid])


def _seed(store):
    stale = store.create(from_agent="seat-a", question="stale q?", op_key="op-1",
                         worker_kind="pane", summary="stale card")
    fresh = store.create(from_agent="seat-b", question="fresh q?", op_key="op-2",
                         worker_kind="pane", summary="fresh card")
    _backdate(store, stale, 3)
    return stale, fresh


def _status(store, rid):
    with sqlite3.connect(store.db_path) as c:
        return c.execute("SELECT status FROM approval_requests WHERE id=?", [rid]).fetchone()[0]


def test_dry_run_lists_due_rows_and_writes_nothing(store, monkeypatch):
    monkeypatch.setattr(approval_schema, "_expire_pending_enabled", lambda: True, raising=False)
    stale, fresh = _seed(store)
    notices = []
    due = store.expire_due(dry_run=True, notify_fn=lambda row: notices.append(row))
    assert [r["id"] for r in due] == [stale]
    assert due[0]["from_agent"] == "seat-a" and due[0]["summary"] == "stale card"
    assert _status(store, stale) == "pending"          # nothing written
    assert notices == []                               # nobody told on a dry run


def test_apply_expires_and_notifies_author_once_per_row(store, monkeypatch):
    monkeypatch.setattr(approval_schema, "_expire_pending_enabled", lambda: True, raising=False)
    stale, fresh = _seed(store)
    notices = []
    due = store.expire_due(dry_run=False, notify_fn=lambda row: notices.append(row))
    assert [r["id"] for r in due] == [stale]
    assert _status(store, stale) == "expired"
    assert _status(store, fresh) == "pending"
    assert [n["id"] for n in notices] == [stale]
    assert notices[0]["from_agent"] == "seat-a"
    # idempotent: a second sweep finds nothing and tells nobody again
    assert store.expire_due(dry_run=False, notify_fn=lambda row: notices.append(row)) == []
    assert len(notices) == 1


def test_notify_failure_never_blocks_the_sweep(store, monkeypatch):
    monkeypatch.setattr(approval_schema, "_expire_pending_enabled", lambda: True, raising=False)
    stale, _ = _seed(store)
    def boom(row):
        raise RuntimeError("msg_store down")
    due = store.expire_due(dry_run=False, notify_fn=boom)
    assert [r["id"] for r in due] == [stale]
    assert _status(store, stale) == "expired"
    assert due[0].get("notified") is False


def test_shaw_gate_off_means_no_expiry_even_on_apply(store, monkeypatch):
    monkeypatch.setattr(approval_schema, "_expire_pending_enabled", lambda: False, raising=False)
    stale, _ = _seed(store)
    assert store.expire_due(dry_run=False, notify_fn=lambda row: None) == []
    assert _status(store, stale) == "pending"


def test_legacy_call_shape_still_returns_ids(store, monkeypatch):
    """Existing callers/tests use expire_due() -> [id, ...]; keep that shape."""
    monkeypatch.setattr(approval_schema, "_expire_pending_enabled", lambda: True, raising=False)
    stale, _ = _seed(store)
    assert store.expire_due() == [stale]


def test_cli_expire_sweep_is_dry_run_by_default(store, monkeypatch, capsys):
    import approval
    monkeypatch.setattr(approval_schema, "_expire_pending_enabled", lambda: True, raising=False)
    monkeypatch.setattr(approval, "_store", lambda: store)
    stale, _ = _seed(store)
    rc = approval.main(["expire-sweep"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 0 and out["dry_run"] is True and out["due"][0]["id"] == stale
    assert _status(store, stale) == "pending"


def test_cli_expire_sweep_apply_writes_and_notifies(store, monkeypatch, capsys):
    import approval
    monkeypatch.setattr(approval_schema, "_expire_pending_enabled", lambda: True, raising=False)
    monkeypatch.setattr(approval, "_store", lambda: store)
    sent = []
    monkeypatch.setattr(approval, "_expiry_notice", lambda row: sent.append(row) or True)
    stale, _ = _seed(store)
    rc = approval.main(["expire-sweep", "--apply"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 0 and out["dry_run"] is False and out["expired"] == 1
    assert _status(store, stale) == "expired"
    assert [s["id"] for s in sent] == [stale]
