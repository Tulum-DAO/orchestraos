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
