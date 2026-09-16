"""RED-first tests for real_seams — the stage-4 wrappers that fill bg_arm's
injected seams against the LIVE identity store. NO orchestration change; these
are thin adapters (DI targets) only.

Load-bearing tests:
  - make_swap_fn wraps swap_generation, builds documents=, default
    sync_effects_owner=False (the async r-a-b contract).
  - FAIL-CLOSED WRITES NOTHING: a swap against a store with no lineage/canonical
    row raises SwapPreconditionError and leaves source_records + canonical
    UNCHANGED (zero rows written) — the whole point of documents-in-txn.
  - a False return on our path (cutover inactive) is an INVARIANT VIOLATION and
    is raised as a hard error (bg_enabled⊆cutover guarantees cutover active).
"""
import os
import sys

import pytest

sys.path.insert(0, "scripts")
from identity_store import orchestra_db  # noqa: E402
from identity_store.orchestra_db import get_connection, init_db  # noqa: E402
from lineage_daemon.wal.real_seams import (  # noqa: E402
    make_swap_fn, SwapInvariantViolation)


def _seed(tmp_path, with_lineage=True):
    od = str(tmp_path)
    os.makedirs(os.path.join(od, "state"), exist_ok=True)
    db = os.path.join(od, "state", "orchestra-registry.db")
    init_db(db)
    if with_lineage:
        conn = get_connection(db)
        conn.execute("INSERT INTO lineages (root, tier, runtime) VALUES "
                     "('identity-store-builder','T2','claude')")
        blue = conn.execute(
            "INSERT INTO generations (root, generation, model) "
            "VALUES ('identity-store-builder', 2, 'claude-opus-4-8[1m]')").lastrowid
        conn.execute("INSERT INTO runtime_state (generation_id, status) "
                     "VALUES (?, 'online')", (blue,))
        conn.execute("INSERT INTO canonical (root, generation_id, tmux_session) "
                     "VALUES ('identity-store-builder', ?, 'identity-store-builder')",
                     (blue,))
        conn.close()
    # arm cutover via env (controlled-run override the seam honors)
    os.environ["IDENTITY_STORE_CUTOVER"] = "1"
    return od, db


def _green():
    return {"generation": 3, "session_id": "new-sid", "model": "claude-opus-4-8[1m]"}


def _blue_record():
    return {"name": "identity-store-builder", "generation": 2,
            "session_id": "0ed9c5d7", "status": "online", "tier": "T2"}


def _counts(db):
    conn = get_connection(db)
    try:
        sr = conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]
        canon = conn.execute(
            "SELECT g.generation FROM canonical c JOIN generations g "
            "ON c.generation_id=g.id WHERE c.root='identity-store-builder'"
        ).fetchone()
        return sr, (canon[0] if canon else None)
    finally:
        conn.close()


def teardown_function(_):
    os.environ.pop("IDENTITY_STORE_CUTOVER", None)


def test_swap_fn_promotes_and_writes_documents(tmp_path):
    od, db = _seed(tmp_path, with_lineage=True)
    swap_fn = make_swap_fn(od, blue_record=_blue_record())
    outcome = swap_fn("identity-store-builder", _green(), blue_generation_id=None)
    assert outcome.status in ("complete", "effects-incomplete")
    sr, canon_gen = _counts(db)
    assert canon_gen == 3                  # canonical moved to Green
    assert sr >= 4                         # documents persisted (4 records)


def test_fail_closed_writes_nothing(tmp_path):
    # store with NO lineage row -> SwapPreconditionError -> ZERO rows written
    od, db = _seed(tmp_path, with_lineage=False)
    swap_fn = make_swap_fn(od, blue_record=_blue_record())
    sr_before, _ = _counts(db)
    with pytest.raises(orchestra_db_precondition_error()):
        swap_fn("identity-store-builder", _green(), blue_generation_id=None)
    sr_after, canon = _counts(db)
    assert sr_after == sr_before == 0      # documents NOT written on fail-closed
    assert canon is None                   # no canonical established


def test_false_return_is_invariant_violation(tmp_path):
    # cutover inactive -> swap_generation returns False -> our path treats it as a
    # hard error (bg_enabled⊆cutover means we should never reach here with cutover off)
    od, db = _seed(tmp_path, with_lineage=True)
    os.environ.pop("IDENTITY_STORE_CUTOVER", None)   # cutover OFF
    swap_fn = make_swap_fn(od, blue_record=_blue_record())
    with pytest.raises(SwapInvariantViolation):
        swap_fn("identity-store-builder", _green(), blue_generation_id=None)


def orchestra_db_precondition_error():
    from identity_store.identity_writer import SwapPreconditionError
    return SwapPreconditionError
