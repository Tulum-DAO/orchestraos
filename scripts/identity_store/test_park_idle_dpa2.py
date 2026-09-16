"""Writer-resume p2 — park-idle retire wired to DP-A2 full-record persistence.

Drives park-idle's ``retire()`` end-to-end under cutover and proves the faithful
shape: canonical dropped, the registry source doc REMOVED (park-idle pops
registry.agents), and the retired session doc KEPT (status=retired + resume fields)
so the agent stays resumable — all reflected by ``project_faithful``. Flag-off stays
byte-identical (INERT).
"""
import importlib.util
import json
import os

import pytest

from scripts.identity_store import cutover, orchestra_db, projector

_PARK_IDLE = os.path.join(os.path.dirname(__file__), "..", "park-idle.py")


def _load_module(orchestra_dir, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(orchestra_dir))
    monkeypatch.delenv("IDENTITY_STORE_CUTOVER", raising=False)
    spec = importlib.util.spec_from_file_location("parkidle_p2", _PARK_IDLE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    return str(tmp_path)


def _seed(orchdir):
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root, tier, runtime) VALUES ('a1','T2','claude')")
    gid = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                    "VALUES ('a1',1,'s1','m')").lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES ('a1',?,'a1','online')", (gid,))
    c.execute("INSERT INTO runtime_state (generation_id, status, last_updated) "
              "VALUES (?,'online','t0')", (gid,))
    for file, kind, key, doc in [
        ("registry.json", "agent", "a1", {"name": "a1", "status": "online"}),
        ("agent-sessions.json", "session", "a1",
         {"session_id": "s1", "status": "online", "resumable": True,
          "resume_command": "resume a1"}),
    ]:
        c.execute("INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
                  "VALUES (?,?,?,0,?)", (file, kind, key, json.dumps(doc)))
    c.close()
    return gid


def _doc(orchdir, file, kind, key):
    c = orchestra_db.get_connection(
        os.path.join(orchdir, "state", "orchestra-registry.db"))
    row = c.execute("SELECT payload_json FROM source_records "
                    "WHERE file=? AND kind=? AND key=?", (file, kind, key)).fetchone()
    c.close()
    return json.loads(row["payload_json"]) if row else None


def test_park_idle_db_retire_keeps_retired_session(orchdir, monkeypatch):
    _seed(orchdir)
    cutover.arm(orchdir)
    m = _load_module(orchdir, monkeypatch)
    retired_session = {"session_id": "s1", "status": "retired", "resumable": True,
                       "resume_command": "resume a1"}
    assert m._db_retire("a1", "idle", retired_session) is True
    # registry doc removed, retired session doc kept:
    assert _doc(orchdir, "registry.json", "agent", "a1") is None
    assert _doc(orchdir, "agent-sessions.json", "session", "a1") == retired_session
    c = orchestra_db.get_connection(
        os.path.join(orchdir, "state", "orchestra-registry.db"))
    assert c.execute("SELECT 1 FROM canonical WHERE root='a1'").fetchone() is None
    c.close()


def test_park_idle_retire_full_path_reflected_by_projector(orchdir, monkeypatch, tmp_path):
    _seed(orchdir)
    cutover.arm(orchdir)
    m = _load_module(orchdir, monkeypatch)
    # drive the real retire() with in-memory reg/meta/roster (as main() would):
    reg = {"agents": {"a1": {"name": "a1", "status": "online"}}}
    meta = {"a1": {"session_id": "s1", "status": "online", "resumable": True,
                   "resume_command": "resume a1"}}
    roster = {"a1": {}}
    monkeypatch.setattr(m, "tmux", lambda *a, **k: None)
    monkeypatch.setattr(m, "_atomic_write", lambda *a, **k: None)
    assert m.retire("a1", reg, meta, roster, "idle") is True

    c = orchestra_db.get_connection(
        os.path.join(orchdir, "state", "orchestra-registry.db"))
    out = tmp_path / "faithful"
    projector.project_faithful(c, str(out))
    c.close()
    proj_reg = json.loads((out / "registry.json").read_text())
    proj_sess = json.loads((out / "state" / "agent-sessions.json").read_text())
    assert "a1" not in proj_reg["agents"], "retired agent removed from registry.agents"
    assert proj_sess.get("a1", {}).get("status") == "retired", "retired session KEPT"
    assert proj_sess["a1"].get("resume_command") == "resume a1", "resume field preserved"


def test_park_idle_inert_flag_off_no_db_write(orchdir, monkeypatch):
    _seed(orchdir)  # DB present but flag OFF
    m = _load_module(orchdir, monkeypatch)
    before = _doc(orchdir, "registry.json", "agent", "a1")
    assert m._db_retire("a1", "idle", {"status": "retired"}) is False
    # INERT: flag-off path did not touch the store
    assert _doc(orchdir, "registry.json", "agent", "a1") == before
