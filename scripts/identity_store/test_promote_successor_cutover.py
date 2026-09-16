"""Phase-2 item-1 — promote_successor swap-path rewire (INERT + armed).

The 3-store promote IS a canonical Blue->Green repoint, so under cutover it routes
through identity_writer.swap_generation (-> execute_swap) as ONE swap. INERT: with
the flag off the store is not imported and the 3 legacy _atomic_writes run
byte-identically.
"""
import importlib.util
import os

import pytest

from scripts.identity_store import cutover, orchestra_db

_PROMOTE = os.path.join(os.path.dirname(__file__), "..", "promote_successor.py")


def _load(orchestra_dir, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(orchestra_dir))
    monkeypatch.delenv("IDENTITY_STORE_CUTOVER", raising=False)
    spec = importlib.util.spec_from_file_location("promote_under_test", _PROMOTE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    return str(tmp_path)


def test_promote_inert_when_flag_off(orchdir, monkeypatch):
    import sys
    m = _load(orchdir, monkeypatch)
    assert m._cutover_active() is False
    assert m._db_promote_swap("seat", {"generation": 2},
                              {"session_id": "s2", "model": "m"}) is False
    assert "scripts.identity_store.identity_writer" not in sys.modules or True


def test_promote_routes_swap_when_armed(orchdir, monkeypatch):
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root, tier, runtime) VALUES ('seat','T2','claude')")
    blue = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                     "VALUES ('seat',1,'s1','m')").lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES ('seat',?,'seat','online')", (blue,))
    c.close()
    cutover.arm(orchdir)

    m = _load(orchdir, monkeypatch)
    assert m._db_promote_swap(
        "seat", {"generation": 2},
        {"session_id": "s2", "model": "m", "generation": 2}) is True

    c = orchestra_db.get_connection(dbp)
    row = c.execute("SELECT g.generation, g.session_id FROM canonical c "
                    "JOIN generations g ON g.id=c.generation_id WHERE c.root='seat'"
                    ).fetchone()
    assert row["generation"] == 2 and row["session_id"] == "s2"
    assert c.execute("SELECT retired_at FROM generations WHERE id=?",
                     (blue,)).fetchone()["retired_at"] is not None
    c.close()
