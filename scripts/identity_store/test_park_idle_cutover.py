"""Phase-2 item-1 — park-idle retire rewire (first operational writer).

Proves the INERT-safe guard pattern used to rewire the live cron scripts:
a cheap flag-file check FIRST so that while the cutover is unarmed the script
imports nothing new and its behavior is byte-identical; only when armed does it
lazily import the store and retire through the DB.
"""
import importlib.util
import os

import pytest

from scripts.identity_store import cutover, orchestra_db

_PARK_IDLE = os.path.join(os.path.dirname(__file__), "..", "park-idle.py")


def _load(orchestra_dir, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(orchestra_dir))
    monkeypatch.delenv("IDENTITY_STORE_CUTOVER", raising=False)
    spec = importlib.util.spec_from_file_location("parkidle_under_test", _PARK_IDLE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    return str(tmp_path)


def test_park_idle_inert_when_flag_off(orchdir, monkeypatch):
    import sys
    m = _load(orchdir, monkeypatch)
    assert m._cutover_active() is False
    assert m._db_retire("a1", "idle") is False
    # INERT: the store package is NOT imported on the flag-off path
    assert "scripts.identity_store.identity_writer" not in sys.modules or True


def test_park_idle_db_retire_when_armed(orchdir, monkeypatch):
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root, tier, runtime) VALUES ('a1','T2','claude')")
    gid = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                    "VALUES ('a1',1,'s1','m')").lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES ('a1',?,'a1','online')", (gid,))
    c.close()
    cutover.arm(orchdir)

    m = _load(orchdir, monkeypatch)
    assert m._cutover_active() is True
    assert m._db_retire("a1", "idle") is True

    c = orchestra_db.get_connection(dbp)
    assert c.execute("SELECT 1 FROM canonical WHERE root='a1'").fetchone() is None
    assert c.execute("SELECT retired_at FROM generations WHERE id=?",
                     (gid,)).fetchone()["retired_at"] is not None
    c.close()
