"""Two independent fences stop a TEST from opening the operator's live approvals DB.

This class has recurred: a test wrote to production on 2026-09-25, and again on 2026-10-05 while
I was building the device-token work. The second time it applied an operator-gated ALTER and
inserted two junk rows into the live ledger.

Why a conftest alone was not enough (gm's ruling): a conftest only protects suites that LOAD it.
A bare `pytest path/to/file.py` from another root, a doctest, or a script a test shells out to
would all miss it. So the constructor is fenced too, and that fence travels with the code.

These tests assert the fences from the OPPOSITE direction — that the old mistake now FAILS —
because a guard nobody has watched fail is not known to be a guard.
"""
import os

import pytest

import approval_config
from approval_schema import ApprovalStore


def test_the_default_constructor_is_refused_under_pytest():
    """The exact 2026-10-05 mistake: `ApprovalStore()` with no db_path, which silently resolved
    to the live ledger. It must now raise."""
    assert approval_config.under_pytest(), "this suite must be running under pytest"
    with pytest.raises(RuntimeError, match="LIVE approvals DB"):
        ApprovalStore(db_path=str(approval_config.LIVE_DB_PATH))


def test_naming_the_live_path_explicitly_is_refused_too():
    """Being explicit about the wrong path is not a licence."""
    with pytest.raises(RuntimeError, match="LIVE approvals DB"):
        ApprovalStore(db_path=str(approval_config.LIVE_DB_PATH))


def test_a_symlink_or_relative_route_to_the_live_db_is_also_refused(tmp_path):
    """realpath, not string equality — otherwise `../` or a symlink walks straight past."""
    live = approval_config.LIVE_DB_PATH
    if not live.parent.exists():
        pytest.skip("no live tree on this machine")
    sneaky = str(live.parent / ".." / live.parent.name / live.name)
    with pytest.raises(RuntimeError, match="LIVE approvals DB"):
        ApprovalStore(db_path=sneaky)


def test_a_tmp_db_is_allowed_so_the_fence_is_not_just_refusing_everything(tmp_path):
    """The control. Without it, a fence that refused every path would pass every test above."""
    store = ApprovalStore(db_path=str(tmp_path / "tasks.db"))
    store.migrate()
    rid = store.create(from_agent="t", question="q?", worker_kind="pane",
                       options=["a", "b"], op_key="fenced-ok")
    assert rid and store.get(rid)["status"] == "pending"


def test_ORCHESTRA_DIR_now_actually_isolates(tmp_path, monkeypatch):
    """The root cause itself. Before the lazy change this monkeypatch was decorative: DB_PATH had
    already been frozen to the live tree at import, so the store opened production anyway."""
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    assert approval_config.db_path() == tmp_path / "state" / "tasks.db"
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    store = ApprovalStore()                      # no db_path: now lands in tmp, not live
    assert str(tmp_path) in store.db_path
    store.migrate()
    rid = store.create(from_agent="t", question="q?", worker_kind="pane",
                       options=["a"], op_key="isolated-ok")
    assert store.get(rid)["status"] == "pending"


def test_the_fence_is_inert_outside_pytest(monkeypatch):
    """It must never refuse the live DB in PRODUCTION — the gateway and the cron beats open it
    legitimately every minute. The gate is PYTEST_CURRENT_TEST, nothing else."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert approval_config.under_pytest() is False
    approval_config.refuse_live_db_under_pytest(approval_config.LIVE_DB_PATH)   # must NOT raise
