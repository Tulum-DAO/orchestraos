"""RED (BG leg-(ii) M3 — promote-attribution verified BY EFFECT).

DELIVERY-CRITICAL seam (promote): a bg swap that commits but leaves the canonical
generation's TYPED session_id null/wrong is the rotation landmine we have hit
repeatedly. M3 sources the sid (build_obs, tested separately) AND adds a post-commit
VERIFY in the swap seam so a failed attribution RAISES instead of silently landing a
null-sid canonical.

These tests drive the REAL swap seam (make_swap_fn -> identity_writer.swap_generation
-> execute_swap) against a REAL orchestra-registry.db and assert the on-disk DB effect
(canonical/green generations.session_id) — the leg-(i) lesson: verify the decisive seam
by effect, not a mock.

RED until read_generation_session_id + verify_session_id_attributed exist and make_swap_fn
verifies post-commit.
"""
import os

import pytest

from identity_store import orchestra_db
from identity_store.orchestra_db import get_connection, init_db
from lineage_daemon.wal.real_seams import make_swap_fn

ROOT = "identity-store-builder"


def _seed(tmp_path):
    od = str(tmp_path)
    os.makedirs(os.path.join(od, "state"), exist_ok=True)
    db = os.path.join(od, "state", "orchestra-registry.db")
    init_db(db)
    conn = get_connection(db)
    conn.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)",
                 (ROOT, "T2", "claude"))
    blue = conn.execute("INSERT INTO generations (root, generation, model) "
                        "VALUES (?, 2, 'claude-opus-4-8[1m]')", (ROOT,)).lastrowid
    conn.execute("INSERT INTO runtime_state (generation_id, status) VALUES (?, 'online')", (blue,))
    conn.execute("INSERT INTO canonical (root, generation_id, tmux_session) "
                 "VALUES (?, ?, ?)", (ROOT, blue, ROOT))
    conn.close()
    os.environ["IDENTITY_STORE_CUTOVER"] = "1"
    return od, db


def teardown_function(_):
    os.environ.pop("IDENTITY_STORE_CUTOVER", None)


def _blue_record():
    return {"name": ROOT, "generation": 2, "session_id": "blue-sid-2",
            "status": "online", "tier": "T2"}


def _gen_sid(db, generation):
    conn = get_connection(db)
    try:
        row = conn.execute("SELECT session_id FROM generations WHERE root=? AND generation=?",
                           (ROOT, generation)).fetchone()
        return row["session_id"] if row else None
    finally:
        conn.close()


def test_swap_attributes_green_sid_by_effect(tmp_path):
    """The delivery-critical invariant: after the swap, the canonical green gen's
    TYPED session_id IS the green's real sid (not null)."""
    od, db = _seed(tmp_path)
    green = {"generation": 3, "session_id": "real-green-sid-3", "model": "m"}
    swap_fn = make_swap_fn(od, blue_record=_blue_record())
    swap_fn(ROOT, green, blue_generation_id=None)
    assert _gen_sid(db, 3) == "real-green-sid-3", \
        "M3: canonical green gen session_id must equal the green's real sid post-swap"


def test_read_generation_session_id_primitive(tmp_path):
    _, db = _seed(tmp_path)
    conn = get_connection(db)
    try:
        assert orchestra_db.read_generation_session_id(conn, ROOT, 2) is None
        conn.execute("UPDATE generations SET session_id='x' WHERE root=? AND generation=2", (ROOT,))
        conn.commit()
        assert orchestra_db.read_generation_session_id(conn, ROOT, 2) == "x"
        assert orchestra_db.read_generation_session_id(conn, ROOT, 99) is None
    finally:
        conn.close()


def test_verify_raises_when_sid_absent(tmp_path):
    """The verify-raise fires when the green gen carries no sid but one was expected."""
    _, db = _seed(tmp_path)
    conn = get_connection(db)
    try:
        # gen 2 has NULL session_id; expecting 'want-sid' must raise
        with pytest.raises(orchestra_db.SidAttributionError):
            orchestra_db.verify_session_id_attributed(conn, ROOT, 2, "want-sid")
    finally:
        conn.close()


def test_verify_noop_when_no_expected_sid(tmp_path):
    _, db = _seed(tmp_path)
    conn = get_connection(db)
    try:
        orchestra_db.verify_session_id_attributed(conn, ROOT, 2, None)  # must NOT raise
    finally:
        conn.close()
