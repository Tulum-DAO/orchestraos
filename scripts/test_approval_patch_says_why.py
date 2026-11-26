"""`approval.py patch` says why it changed nothing.

A refused patch used to print an empty line and exit 4 with no reason, so a client agent retried the same patch
for several turns. Now every refusal prints one REFUSED line on stderr naming the cause; exit codes are unchanged.

Hermetic: tmp tasks.db via APPROVAL_DB_PATH, HOME a tmp dir, ntfy fenced (a scratch DB alone still lets notify() POST).
"""
import os, subprocess, sys

import pytest
from approval_schema import ApprovalStore

HERE = os.path.dirname(os.path.abspath(__file__))
NO_NTFY = "/nonexistent/ntfy-token-for-tests"


@pytest.fixture(autouse=True)
def fenced(tmp_path, monkeypatch):
    monkeypatch.setenv("NTFY_TOKEN_FILE", NO_NTFY)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("APPROVAL_DDL_ARMED", raising=False)
    # patch now runs the interposer guard: no inherited pane identity (test_approval.py's _no_inherited_tmux_identity)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    assert not os.path.exists(NO_NTFY)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "tasks.db")


@pytest.fixture
def store(db):
    s = ApprovalStore(db_path=db); s.migrate(); return s


def _task(s):
    return s.create(from_agent="builder", question="Plug in the phone", worker_kind="pane",
                    options=["done", "cant", "snooze"], kind="human_task", block_task="Plug in the phone")


def _patch(db, rid, *extra):
    env = dict(os.environ, APPROVAL_DB_PATH=db, NTFY_TOKEN_FILE=NO_NTFY)
    return subprocess.run([sys.executable, os.path.join(HERE, "approval.py"), "patch", "--id", rid,
                           "--from", "builder", *extra], capture_output=True, text=True, env=env, timeout=60)


def test_a_pending_human_task_is_patched_silently(store, db):
    rid = _task(store)
    r = _patch(db, rid, "--summary", "new detail")
    assert r.returncode == 0 and r.stdout.strip() == rid and "REFUSED" not in r.stderr
    assert store.get(rid)["summary"] == "new detail"


def test_a_card_that_is_no_longer_pending_says_so(store, db):
    rid = _task(store)
    c = store._conn()
    c.execute("UPDATE approval_requests SET status='resumed' WHERE id=?", [rid]); c.commit()
    r = _patch(db, rid, "--summary", "late")
    assert r.returncode == 4
    assert f"REFUSED: card {rid} is resumed, not pending" in r.stderr
    assert "file a new card" in r.stderr


def test_an_unknown_card_says_not_found(store, db):
    r = _patch(db, "apr_doesnotexist_1", "--summary", "x")
    assert r.returncode == 4 and "REFUSED: card apr_doesnotexist_1 not found" in r.stderr


def test_an_approval_card_says_patch_is_for_human_tasks(store, db):
    rid = store.create(from_agent="builder", question="Ship it?", worker_kind="pane")
    r = _patch(db, rid, "--summary", "x")
    assert r.returncode == 4 and "patch changes only human_task cards" in r.stderr


def test_no_field_says_nothing_to_change(store, db):
    rid = _task(store)
    r = _patch(db, rid)
    assert r.returncode == 4 and "REFUSED: nothing to change" in r.stderr
