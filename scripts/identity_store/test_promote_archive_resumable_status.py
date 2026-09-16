"""Identity Layer v1 item (b) — the archived predecessor's THREE misses (gm msg_b1429f36).

By effect after the g40->g41 (and g62->g63) promote, the swap-retired predecessor archive
diverged three ways:
  1. the DB generations row had retired_at set but resume_command NULL (the resume lived
     only in the flat/doc — a resumable archive the typed store could not prove resumable);
  2. its runtime_state row stayed status='online' (nothing flipped the archived gen off);
  3. the faithful projector emitted status='retired' in registry.json (hardcoded at
     _build_faithful_registry) but 'parked' in agent-sessions.json (doc served verbatim) —
     the two projections DISAGREED, and M1 (per-file flat==faithful) is blind to it.

Fix: promote finalizes the archive DB-first (resume_command onto the gen row +
runtime_state->parked); the projector decides a NON-canonical (retired) generation's status
by ONE rule — 'parked' if it carries a resume_command (resumable) else 'retired' — applied
IDENTICALLY to both projections.
"""
import importlib.util
import json
import os

import pytest

from scripts.identity_store import cutover, orchestra_db, projector


_PROMOTE = os.path.join(os.path.dirname(__file__), "..", "promote_successor.py")


def _load(orchestra_dir, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(orchestra_dir))
    monkeypatch.delenv("IDENTITY_STORE_CUTOVER", raising=False)
    spec = importlib.util.spec_from_file_location("promote_under_test_archive", _PROMOTE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    return str(tmp_path)


def _seed_archive(dbp, *, resume):
    """A live canonical seat (green gen63) + a swap-retired archive (blue gen62) whose
    source_records docs exist. ``resume`` is the resume_command on the archive gen row."""
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    now = orchestra_db._utcnow()
    c.execute("INSERT INTO lineages (root,tier,runtime,machine,cwd,always_on) "
              "VALUES ('seat','T1','claude','vps','/x',1)")
    green = c.execute("INSERT INTO generations (root,generation,session_id,model,promoted_at) "
                      "VALUES ('seat',63,'sg','m',?)", (now,)).lastrowid
    c.execute("INSERT INTO canonical (root,generation_id,tmux_session,status) "
              "VALUES ('seat',?,'seat','online')", (green,))
    c.execute("INSERT INTO runtime_state (generation_id,status,last_updated) "
              "VALUES (?,'online',?)", (green, now))
    blue = c.execute("INSERT INTO generations (root,generation,session_id,model,"
                     "retired_at,resume_command) VALUES ('seat',62,'sb','m',?,?)",
                     (now, resume)).lastrowid
    c.execute("INSERT INTO runtime_state (generation_id,status,last_updated) "
              "VALUES (?,'parked',?)", (blue, now))
    arch_key = "seat-gen62"
    arch_doc = {"name": arch_key, "lineage_root": "seat", "generation": 62,
                "session_id": "sb", "status": "parked", "resume_command": resume}
    c.execute("INSERT INTO source_records (file,kind,key,ordinal,payload_json) "
              "VALUES ('registry.json','agent',?,0,?)", (arch_key, json.dumps(arch_doc)))
    c.execute("INSERT INTO source_records (file,kind,key,ordinal,payload_json) "
              "VALUES ('agent-sessions.json','session',?,0,?)", (arch_key, json.dumps(arch_doc)))
    # live green docs so registry/sessions carry the canonical member too
    c.execute("INSERT INTO source_records (file,kind,key,ordinal,payload_json) "
              "VALUES ('registry.json','agent','seat',0,?)",
              (json.dumps({"name": "seat", "lineage_root": "seat", "status": "online"}),))
    c.execute("INSERT INTO source_records (file,kind,key,ordinal,payload_json) "
              "VALUES ('agent-sessions.json','session','seat',0,?)",
              (json.dumps({"session_id": "sg", "generation": 63, "status": "online"}),))
    c.commit()
    c.close()
    return arch_key, blue


def _project_and_read(orchdir, dbp):
    c = orchestra_db.get_connection(dbp)
    try:
        projector.project_faithful(c, orchdir)
    finally:
        c.close()
    with open(os.path.join(orchdir, "registry.json")) as fh:
        reg = json.load(fh)
    with open(os.path.join(orchdir, "state", "agent-sessions.json")) as fh:
        sess = json.load(fh)
    return reg, sess


def test_resumable_archive_projects_parked_in_both(orchdir):
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    arch_key, _ = _seed_archive(dbp, resume="claude --resume sb")
    reg, sess = _project_and_read(orchdir, dbp)
    assert reg["agents"][arch_key]["status"] == "parked"
    assert sess[arch_key]["status"] == "parked"
    # the cross-file invariant the new guard enforces
    assert reg["agents"][arch_key]["status"] == sess[arch_key]["status"]


def test_nonresumable_archive_projects_retired_in_both(orchdir):
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    arch_key, _ = _seed_archive(dbp, resume=None)
    reg, sess = _project_and_read(orchdir, dbp)
    assert reg["agents"][arch_key]["status"] == "retired"
    assert sess[arch_key]["status"] == "retired"
    assert reg["agents"][arch_key]["status"] == sess[arch_key]["status"]


def test_promote_finalizes_archive_resume_and_runtime_parked(orchdir, monkeypatch):
    """Fix 1+2: the swap-retired blue generation gets its resume_command written onto
    the DB gen row AND its runtime_state flipped off 'online' to 'parked'."""
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    now = orchestra_db._utcnow()
    c.execute("INSERT INTO lineages (root,tier,runtime) VALUES ('seat','T2','claude')")
    blue = c.execute("INSERT INTO generations (root,generation,session_id,model) "
                     "VALUES ('seat',62,'s1','m')").lastrowid
    c.execute("INSERT INTO canonical (root,generation_id,tmux_session,status) "
              "VALUES ('seat',?,'seat','online')", (blue,))
    c.execute("INSERT INTO runtime_state (generation_id,status,last_updated) "
              "VALUES (?,'online',?)", (blue, now))
    c.close()
    cutover.arm(orchdir)

    m = _load(orchdir, monkeypatch)
    reg = {"agents": {"seat-gen62": {"id": "seat-gen62", "generation": 62,
                                     "resume_command": "claude --resume s1"}}}
    meta = {"seat-gen62": {"status": "parked", "generation": 62,
                           "resume_command": "claude --resume s1"}}
    handled = m._db_promote_swap(
        "seat", {"generation": 63},
        {"session_id": "s2", "model": "m", "generation": 63},
        archive_key="seat-gen62", reg=reg, meta=meta)
    assert handled is True

    c = orchestra_db.get_connection(dbp)
    try:
        row = c.execute("SELECT resume_command, retired_at FROM generations "
                        "WHERE id=?", (blue,)).fetchone()
        assert row["resume_command"] == "claude --resume s1"   # fix 1
        assert row["retired_at"] is not None
        rs = c.execute("SELECT status FROM runtime_state WHERE generation_id=?",
                       (blue,)).fetchone()
        assert rs["status"] == "parked"                        # fix 2
    finally:
        c.close()
