"""Worktree-isolation conftest (parity-builder-r2, R2).

`tests/parity/test_provider_parity_red.py` hardcodes the LIVE tree
(`ORCH = ~/scripts/agent-orchestra`) and does `sys.path.insert(0, ORCH/scripts)`
at module top-level, so an unaided run imports the MAIN tree's `approval_schema`
— not the code under test in this isolated worktree.

pytest imports rootdir conftest.py BEFORE collecting any test module. By
inserting THIS worktree's `scripts/` at the front of `sys.path` and importing
`approval_schema` (+ `approval_config`) here, they land in `sys.modules` first.
The test's later `from approval_schema import ApprovalStore` then resolves to
the cached worktree copy — the test's own `sys.path.insert` no longer matters
because the module is already imported.

Net effect: the RED suite runs against THIS worktree's changes while the live
tree stays untouched (isolation HARD RULE 3). No assertion is altered; this only
redirects WHICH copy of the module-under-test is exercised. Scratch DBs only.
"""
import os
import sys

_WORKTREE_SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
sys.path.insert(0, _WORKTREE_SCRIPTS)

# Force the worktree copies into sys.modules before the test module's own
# top-level sys.path.insert(0, <live>/scripts) runs during collection.
import approval_config  # noqa: E402,F401
import approval_schema  # noqa: E402,F401

# Fail loudly if the wrong copy got cached — proves the isolation held.
assert os.path.dirname(os.path.abspath(approval_schema.__file__)) == _WORKTREE_SCRIPTS, (
    f"approval_schema resolved to {approval_schema.__file__}, "
    f"expected worktree copy under {_WORKTREE_SCRIPTS}"
)


# --- process-wide environ isolation (gm msg_596aaadb, 2026-09-16) ------------------------
# Snapshot os.environ before EVERY test and restore it after. ~20 test files write
# ORCHESTRA_DIR / HOME / *_PATH with a raw os.environ[...] = ... (not monkeypatch) and a few
# never restore; in a single-process full run those leaks redirected later tests' CLI
# subprocesses and module loads to a scratch tree (approval_get_qnr, arturo requires_bearer,
# boundary_delivery REAL resolver: green alone, red in the full run). Module-level
# setdefault()s in scripts/conftest.py run at import, before this fixture, and are kept.
import os as _os
import pytest as _pytest


@_pytest.fixture(autouse=True)
def _restore_environ_between_tests():
    saved = dict(_os.environ)
    yield
    _os.environ.clear()
    _os.environ.update(saved)


# --- the live DB is NOT a test fixture (incident 2026-10-05) ------------------------------
# `approval_config.DB_PATH` is computed at IMPORT time from ORCHESTRA_DIR, defaulting to the
# LIVE tree. So `monkeypatch.setenv("ORCHESTRA_DIR", tmp)` inside a test LOOKS like isolation
# and is not — the path was frozen before the test body ran. A test that then does
# `ApprovalStore()` with no db_path opens Shaw's live tasks.db.
#
# That is not hypothetical: it is exactly how a test in this repo ALTERed the live
# approval_requests table (an operator-gated DDL) and inserted two junk rows into the live
# ledger. The environ-isolation belt above cannot catch it, because nothing about the
# environment is wrong by the time the test runs.
#
# So the live path is refused at the sqlite layer, where the mistake actually lands. A test
# that wants a store passes `db_path=<tmp>`; there is no legitimate reason for a test to open
# the operator's live ledger.
import sqlite3 as _sqlite3

_LIVE_DB = os.path.join(os.path.expanduser("~/scripts/agent-orchestra"), "state", "tasks.db")
_real_connect = _sqlite3.connect


def _guarded_connect(database, *a, **kw):
    try:
        target = os.path.abspath(str(database).split("file:")[-1].split("?")[0])
    except Exception:            # noqa: BLE001 — a URI we cannot parse is not the live path
        target = ""
    if target == os.path.abspath(_LIVE_DB):
        raise AssertionError(
            "a test tried to open the LIVE approvals DB (" + _LIVE_DB + ").\n"
            "DB_PATH is import-time, so monkeypatching ORCHESTRA_DIR does not isolate it.\n"
            "Pass an explicit path instead:  ApprovalStore(db_path=str(tmp_path / 'tasks.db'))")
    return _real_connect(database, *a, **kw)


@_pytest.fixture(autouse=True)
def _refuse_the_live_db(monkeypatch):
    monkeypatch.setattr(_sqlite3, "connect", _guarded_connect)
    yield
