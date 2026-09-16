"""execute_swap (the SHARED swap primitive both _db_promote_swap AND the BG make_swap_fn
call) must retire the leftover successor-alias canonical row DB-first, IN the swap txn — so
NEITHER path can leak it (gm msg_6eda7033). The BG/autonomous fire path previously left
`<root>-g<green_gen>` status=online (sid None, no pane) after the swap — the phantom-canonical
accumulation class, recurring on every autonomous fire.

RED test: a swap that promotes green gen N leaves ZERO `<root>-g<N>` alias canonical rows."""
import os

from scripts.identity_store import orchestra_db


def _seed_with_alias(dbp):
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    # the live seat (blue gen1 canonical)
    c.execute("INSERT INTO lineages (root,tier,runtime) VALUES ('seat','T2','gemini')")
    blue = c.execute("INSERT INTO generations (root,generation,session_id,model) "
                     "VALUES ('seat',1,'blue','m')").lastrowid
    c.execute("INSERT INTO canonical (root,generation_id,tmux_session,status) "
              "VALUES ('seat',?,'seat','online')", (blue,))
    # the BG spawn registered the successor under its OWN temp lineage `seat-g2`
    # (its own canonical row, generation 1, sid NULL) — the leak shape.
    c.execute("INSERT INTO lineages (root,tier,runtime) VALUES ('seat-g2','T2','gemini')")
    alias_gen = c.execute("INSERT INTO generations (root,generation,session_id,model) "
                          "VALUES ('seat-g2',1,NULL,'m')").lastrowid
    c.execute("INSERT INTO canonical (root,generation_id,tmux_session,status) "
              "VALUES ('seat-g2',?,'seat-g2','online')", (alias_gen,))
    c.commit()
    return c, blue, alias_gen


def test_bg_swap_leaves_zero_alias_canonical_rows(tmp_path):
    dbp = os.path.join(tmp_path, "orchestra-registry.db")
    c, blue, alias_gen = _seed_with_alias(dbp)
    # a BG swap promotes green gen2 under 'seat' (make_swap_fn -> swap_generation ->
    # execute_swap). No aliases_retired list is threaded on this path.
    orchestra_db.execute_swap(
        c, "seat", {"generation": 2, "session_id": "green", "model": "m"},
        blue_generation_id=blue)
    # the deterministic alias canonical row `seat-g2` must be GONE (DB-first retire) ...
    assert c.execute("SELECT 1 FROM canonical WHERE root='seat-g2'").fetchone() is None
    # ... its generation retired (resumable/archived) ...
    assert c.execute("SELECT retired_at FROM generations WHERE id=?", (alias_gen,)
                     ).fetchone()["retired_at"] is not None
    # ... and the lineage row LEFT (retire never drops lineages).
    assert c.execute("SELECT 1 FROM lineages WHERE root='seat-g2'").fetchone() is not None
    # the promoted seat is intact.
    row = c.execute("SELECT g.generation FROM canonical c JOIN generations g "
                    "ON g.id=c.generation_id WHERE c.root='seat'").fetchone()
    assert row["generation"] == 2
    c.close()


def test_swap_no_alias_row_is_noop(tmp_path):
    """No `<root>-g<N>` alias present -> the swap still works, nothing spurious retired."""
    dbp = os.path.join(tmp_path, "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root,tier,runtime) VALUES ('seat','T2','claude')")
    blue = c.execute("INSERT INTO generations (root,generation,session_id,model) "
                     "VALUES ('seat',1,'b','m')").lastrowid
    c.execute("INSERT INTO canonical (root,generation_id,tmux_session,status) "
              "VALUES ('seat',?,'seat','online')", (blue,))
    c.commit()
    orchestra_db.execute_swap(
        c, "seat", {"generation": 2, "session_id": "g", "model": "m"},
        blue_generation_id=blue)
    assert c.execute("SELECT g.generation FROM canonical c JOIN generations g "
                     "ON g.id=c.generation_id WHERE c.root='seat'").fetchone()["generation"] == 2
    c.close()
