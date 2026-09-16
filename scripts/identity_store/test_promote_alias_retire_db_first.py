"""Identity Layer v1 item (a) — promote must retire successor-alias rows DB-FIRST.

Root cause (gm-diagnosed by effect from the g40->g41 promote, msg_b1429f36):
promote_successor.py retired leftover successor-alias rows in the FLAT agents/sessions
dicts ONLY (:1341-1365 / :1506-1528) — the alias's own DB canonical row was never
retired. By effect the store kept e.g. `orchestra-builder-g41` status='online' (gen 1,
sid None) with NO pane while registry.json said 'retired': flat and DB disagree, and the
next faithful projection can RESURRECT the alias (the "3 alias-resurrections 2026-08-18"
class at :1499, patched flat-side only).

FIX: under cutover the swap-path retires each alias DB-first via the sanctioned
retire_agent (canonical row removed, alias generation retired_at stamped, lineage row
left); flat follows via project_now, the flat edits stay belt-and-braces.
"""
import importlib.util
import os

import pytest

from scripts.identity_store import cutover, orchestra_db

_PROMOTE = os.path.join(os.path.dirname(__file__), "..", "promote_successor.py")


def _load(orchestra_dir, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(orchestra_dir))
    monkeypatch.delenv("IDENTITY_STORE_CUTOVER", raising=False)
    spec = importlib.util.spec_from_file_location("promote_under_test_alias", _PROMOTE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    return str(tmp_path)


def _seed(dbp):
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    # the seat (blue) being promoted
    c.execute("INSERT INTO lineages (root, tier, runtime) VALUES ('seat','T2','claude')")
    blue = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                     "VALUES ('seat',1,'s1','m')").lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES ('seat',?,'seat','online')", (blue,))
    # a leftover successor-alias canonical row (the leak class): its OWN lineage +
    # canonical row, status='online', NO live process (sid None) — exactly the shape
    # promote leaves behind for `<root>-gN`.
    c.execute("INSERT INTO lineages (root, tier, runtime) VALUES ('seat-g2','T2','claude')")
    alias_gen = c.execute("INSERT INTO generations (root, generation, model) "
                          "VALUES ('seat-g2',1,'m')").lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES ('seat-g2',?,'seat-g2','online')", (alias_gen,))
    c.close()
    return blue, alias_gen


def test_promote_retires_alias_canonical_row_db_first(orchdir, monkeypatch):
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    _blue, alias_gen = _seed(dbp)
    cutover.arm(orchdir)

    m = _load(orchdir, monkeypatch)
    handled = m._db_promote_swap(
        "seat", {"generation": 2},
        {"session_id": "s2", "model": "m", "generation": 2},
        aliases_retired=["seat-g2"])
    assert handled is True

    c = orchestra_db.get_connection(dbp)
    try:
        # DB-FIRST: the alias canonical row is GONE (not merely flat-retired).
        assert c.execute("SELECT 1 FROM canonical WHERE root='seat-g2'").fetchone() is None
        # its generation row is retired (resumable archive) ...
        assert c.execute("SELECT retired_at FROM generations WHERE id=?",
                         (alias_gen,)).fetchone()["retired_at"] is not None
        # ... and the lineage row is LEFT (retire_agent never drops lineages).
        assert c.execute("SELECT 1 FROM lineages WHERE root='seat-g2'").fetchone() is not None
        # the promoted seat is unaffected.
        row = c.execute("SELECT g.generation, g.session_id FROM canonical c "
                        "JOIN generations g ON g.id=c.generation_id "
                        "WHERE c.root='seat'").fetchone()
        assert row["generation"] == 2 and row["session_id"] == "s2"
    finally:
        c.close()


def test_promote_shared_primitive_retires_deterministic_alias_without_list(orchdir, monkeypatch):
    """Even with NO aliases_retired list, the SHARED swap primitive (execute_swap) retires
    the deterministic `<root>-g<green_gen>` alias in-txn (gm msg_6eda7033) — so the BG/
    autonomous path, which threads no list, cannot leak it. The seed's alias is `seat-g2`
    and green is gen 2, so it is retired DB-first."""
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    _seed(dbp)
    cutover.arm(orchdir)
    m = _load(orchdir, monkeypatch)
    assert m._db_promote_swap(
        "seat", {"generation": 2},
        {"session_id": "s2", "model": "m", "generation": 2}) is True
    c = orchestra_db.get_connection(dbp)
    try:
        # the deterministic alias canonical row is GONE, its lineage KEPT.
        assert c.execute("SELECT 1 FROM canonical WHERE root='seat-g2'").fetchone() is None
        assert c.execute("SELECT 1 FROM lineages WHERE root='seat-g2'").fetchone() is not None
    finally:
        c.close()
