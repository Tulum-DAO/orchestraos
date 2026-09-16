"""DEFECT (ios-watch-dev g16->17, gm msg_0a37acf3): rotate_agent computes succ_gen =
pred_gen+1; when a RETIRED phantom generation row already occupies (root, succ_gen), the
promote REUSES it (execute_swap -> _resolve_or_insert_green reuse branch) but leaves
retired_at set + promoted_at NULL, then repoints canonical to it. Result: canonical points
at a generation every canonical-live definition (fleet guard: retired_at IS NULL) reads as
DEAD. The rotation is real (pane live) but the store says the seat is retired.

FIX (gm option b): in the SAME txn as the canonical repoint, clear retired_at + stamp
promoted_at/promoted_by on the promoted green, and a post-promote invariant assert
(canonical.gen.retired_at IS NULL AND promoted_at IS NOT NULL) fail-closed LOUD.
"""
import sqlite3
import sys

sys.path.insert(0, "scripts")
from identity_store import orchestra_db  # noqa: E402


def _db(tmp_path):
    p = str(tmp_path / "reg.db")
    orchestra_db.init_db(p)
    conn = orchestra_db.get_connection(p)
    conn.execute("INSERT INTO lineages(root,tier,runtime,machine) VALUES('r','T2','codex','vps')")
    b = conn.execute(
        "INSERT INTO generations(root,generation,session_id,model,promoted_at) "
        "VALUES('r',16,'sid-blue','m','2026-01-01T00:00:00Z')").lastrowid
    conn.execute("INSERT INTO canonical(root,generation_id,tmux_session,status) "
                 "VALUES('r',?,'r','online')", (b,))
    conn.commit()
    return conn, b


def _canon_gen(conn):
    return conn.execute(
        "SELECT g.generation, g.session_id, g.retired_at, g.promoted_at "
        "FROM canonical c JOIN generations g ON g.id=c.generation_id "
        "WHERE c.root='r'").fetchone()


def test_promote_onto_retired_phantom_leaves_canonical_live(tmp_path):
    conn, blue = _db(tmp_path)
    # a RETIRED phantom green already occupies gen 17 (the row-581 shape)
    conn.execute(
        "INSERT INTO generations(root,generation,session_id,model,retired_at) "
        "VALUES('r',17,NULL,'m','2026-09-14T04:00:15')")
    conn.commit()
    orchestra_db.execute_swap(
        conn, "r",
        {"generation": 17, "session_id": "sid-green", "model": "m",
         "tmux_session": "r", "promoted_by": "rotate_agent"},
        blue_generation_id=blue, sync_effects_owner=True)
    row = _canon_gen(conn)
    assert row["session_id"] == "sid-green"
    assert row["retired_at"] is None, f"canonical points at a RETIRED gen (retired_at={row['retired_at']})"
    assert row["promoted_at"] is not None, "promoted canonical gen has no promoted_at"


def test_promote_fresh_insert_still_stamps_promoted_at(tmp_path):
    # no phantom at 17 -> INSERT path; the promoted green must still be live+stamped.
    conn, blue = _db(tmp_path)
    orchestra_db.execute_swap(
        conn, "r",
        {"generation": 17, "session_id": "sid-green", "model": "m",
         "tmux_session": "r", "promoted_by": "rotate_agent"},
        blue_generation_id=blue, sync_effects_owner=True)
    row = _canon_gen(conn)
    assert row["generation"] == 17 and row["retired_at"] is None
    assert row["promoted_at"] is not None


def test_blue_is_retired_after_promote(tmp_path):
    conn, blue = _db(tmp_path)
    orchestra_db.execute_swap(
        conn, "r",
        {"generation": 17, "session_id": "sid-green", "model": "m", "tmux_session": "r"},
        blue_generation_id=blue, sync_effects_owner=True)
    br = conn.execute("SELECT retired_at FROM generations WHERE id=?", (blue,)).fetchone()
    assert br["retired_at"] is not None  # blue retired (unchanged behavior)
