"""Writer-resume p3b — rotate_agent provisional register/prune rewire.

rotate_agent's OWN registry writes (Step-3 successor registration + the hold/abort
prune) route to the store under cutover:
  * Step-3 (:477) -> a PROVISIONAL (non-canonical, live) generation via
    register_provisional; project_faithful synthesizes the <root>-g<N> alias.
  * _prune_registered_successor (:356) -> prune_provisional deletes that
    provisional generation (the alias disappears), guarded against nuking a
    promoted/retired identity.
INERT: flag-off keeps the byte-identical direct-JSON registration/prune.
"""
import importlib.util
import json
import os

import pytest

from scripts.identity_store import (cutover, identity_writer, orchestra_db,
                                     projector)

ROOT = "a1"
_ROTATE = os.path.join(os.path.dirname(__file__), "..", "rotate_agent.py")


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    return str(tmp_path)


def _seed(orchdir, with_docs=True):
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root, tier, runtime, machine, cwd, always_on) "
              "VALUES ('a1','T2','claude','vps','/x',1)")
    gid = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                    "VALUES ('a1',1,'s1','m')").lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES ('a1',?,'a1','online')", (gid,))
    if with_docs:
        c.execute("INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
                  "VALUES ('registry.json','agent','a1',0,?)",
                  (json.dumps({"name": "a1", "system_prompt": "prompts/a1.md",
                               "generation": 1, "status": "online"}),))
    c.close()
    return gid


def _conn(orchdir):
    return orchestra_db.get_connection(
        os.path.join(orchdir, "state", "orchestra-registry.db"))


# --- identity_writer.register_provisional ----------------------------------

def test_register_provisional_inactive_returns_false(orchdir):
    _seed(orchdir)
    assert identity_writer.register_provisional(orchdir, ROOT, 2, model="m") is False


def test_register_provisional_fail_closed_no_lineage(orchdir):
    _seed(orchdir)
    cutover.arm(orchdir)
    with pytest.raises(identity_writer.SwapPreconditionError):
        identity_writer.register_provisional(orchdir, "no-such-root", 2, model="m")


def test_register_provisional_inserts_noncanonical_live_gen(orchdir):
    _seed(orchdir)
    cutover.arm(orchdir)
    assert identity_writer.register_provisional(orchdir, ROOT, 2, model="m") is True
    c = _conn(orchdir)
    row = c.execute("SELECT id, retired_at, session_id FROM generations "
                    "WHERE root=? AND generation=2", (ROOT,)).fetchone()
    assert row is not None and row["retired_at"] is None and row["session_id"] is None
    # NOT canonical (predecessor keeps the seat):
    assert c.execute("SELECT g.generation FROM canonical c JOIN generations g "
                     "ON g.id=c.generation_id WHERE c.root=?", (ROOT,)).fetchone()[0] == 1
    c.close()
    # idempotent:
    assert identity_writer.register_provisional(orchdir, ROOT, 2, model="m") is True


def test_register_provisional_projects_alias(orchdir, tmp_path):
    _seed(orchdir)
    cutover.arm(orchdir)
    identity_writer.register_provisional(orchdir, ROOT, 2, model="claude-x")
    c = _conn(orchdir)
    out = tmp_path / "faithful"
    projector.project_faithful(c, str(out))
    c.close()
    reg = json.loads((out / "registry.json").read_text())
    assert "a1-g2" in reg["agents"], "provisional gen must project the <root>-g<N> alias"
    alias = reg["agents"]["a1-g2"]
    assert alias["status"] == "provisioning"
    assert alias["generation"] == 2
    assert alias["lineage_root"] == ROOT
    assert alias["system_prompt"] == "prompts/a1.md"  # from the root's live doc
    assert "a1-g2" not in json.loads(
        (out / "state" / "agent-sessions.json").read_text()), "registry-only (P4)"


# --- identity_writer.prune_provisional -------------------------------------

def test_prune_provisional_deletes_live_provisional(orchdir, tmp_path):
    _seed(orchdir)
    cutover.arm(orchdir)
    identity_writer.register_provisional(orchdir, ROOT, 2, model="m")
    assert identity_writer.prune_provisional(orchdir, ROOT, 2) is True
    c = _conn(orchdir)
    assert c.execute("SELECT 1 FROM generations WHERE root=? AND generation=2",
                     (ROOT,)).fetchone() is None
    out = tmp_path / "faithful"
    projector.project_faithful(c, str(out))
    c.close()
    assert "a1-g2" not in json.loads((out / "registry.json").read_text())["agents"]


def test_prune_provisional_refuses_canonical(orchdir):
    """A canonical generation must NEVER be pruned by hold-cleanup (safe no-op)."""
    _seed(orchdir)
    cutover.arm(orchdir)
    assert identity_writer.prune_provisional(orchdir, ROOT, 1) is True  # gen1 is canonical
    c = _conn(orchdir)
    assert c.execute("SELECT 1 FROM generations WHERE root=? AND generation=1",
                     (ROOT,)).fetchone() is not None, "canonical gen preserved"
    c.close()


def test_prune_provisional_refuses_retired(orchdir):
    _seed(orchdir)
    cutover.arm(orchdir)
    identity_writer.register_provisional(orchdir, ROOT, 2, model="m")
    c = _conn(orchdir)
    c.execute("UPDATE generations SET retired_at='t' WHERE root=? AND generation=2",
              (ROOT,))
    c.close()
    assert identity_writer.prune_provisional(orchdir, ROOT, 2) is True
    c = _conn(orchdir)
    assert c.execute("SELECT 1 FROM generations WHERE root=? AND generation=2",
                     (ROOT,)).fetchone() is not None, "retired gen preserved (not pruned)"
    c.close()


def test_prune_provisional_inactive_returns_false(orchdir):
    _seed(orchdir)
    assert identity_writer.prune_provisional(orchdir, ROOT, 2) is False


# --- rotate_agent seam (INERT + armed routing) -----------------------------

def _load_rotate():
    spec = importlib.util.spec_from_file_location("rotate_p3b", _ROTATE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_rotate_agent_seam_inert_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("IDENTITY_STORE_CUTOVER", raising=False)
    m = _load_rotate()
    # HERMETICITY: point the cutover-dir global at a flag-less sandbox so
    # _cutover_active() does NOT read the live armed flag at the real ORCHESTRA_DIR.
    from pathlib import Path
    monkeypatch.setattr(m, "ORCHESTRA_DIR", Path(tmp_path))
    assert m._cutover_active() is False
    # flag-off: returns False WITHOUT importing the store or touching anything
    assert m._db_register_provisional("a1", 2, "m") is False
    assert m._db_prune_provisional("a1-g2") is False


def test_rotate_agent_seam_armed_routes_to_store(orchdir, monkeypatch):
    _seed(orchdir)
    m = _load_rotate()
    # redirect runtime writes to the tmp sandbox; arm via env (hermetic)
    from pathlib import Path
    monkeypatch.setattr(m, "ORCHESTRA_DIR", Path(orchdir))
    monkeypatch.setenv("IDENTITY_STORE_CUTOVER", "1")
    assert m._cutover_active() is True
    assert m._db_register_provisional("a1", 2, "claude-x") is True
    c = _conn(orchdir)
    assert c.execute("SELECT 1 FROM generations WHERE root='a1' AND generation=2"
                     ).fetchone() is not None
    c.close()
    # prune derives (root, gen) from the alias and deletes the provisional row
    assert m._db_prune_provisional("a1-g2") is True
    c = _conn(orchdir)
    assert c.execute("SELECT 1 FROM generations WHERE root='a1' AND generation=2"
                     ).fetchone() is None
    c.close()


# --- DEFECT FIX (gm msg_92f7ef2d): UPSERT stale model on a reused successor gen row ---

def test_register_provisional_upserts_stale_model_on_existing_gen(orchdir):
    """THE DEFECT: a re-registered successor at an EXISTING (retired/provisional) gen row
    kept the STALE model (fable) -> the spawn resolved fable -> credit-walled DOA.
    register_provisional must UPSERT the model so every rotation spawns the successor on
    the canonical's CURRENT model, closing the class (gm's 565 hand-patch fixed only one)."""
    _seed(orchdir)
    cutover.arm(orchdir)
    c = _conn(orchdir)
    c.execute("INSERT INTO generations (root, generation, session_id, model) "
              "VALUES ('a1',2,NULL,'claude-fable-5[1m]')")   # stale reused successor slot
    c.close()
    assert identity_writer.register_provisional(
        orchdir, ROOT, 2, model="claude-opus-4-8[1m]") is True
    c = _conn(orchdir)
    row = c.execute("SELECT model FROM generations WHERE root='a1' AND generation=2").fetchone()
    c.close()
    assert row[0] == "claude-opus-4-8[1m]", \
        "register_provisional must UPSERT the stale model on the reused successor gen row"


def test_register_provisional_never_restamps_canonical_model(orchdir):
    """SAFETY: register_provisional must NEVER overwrite the CANONICAL generation's model
    (gen1 is canonical in _seed) — only a non-canonical provisional/successor slot."""
    _seed(orchdir)   # gen1 model 'm' is canonical
    cutover.arm(orchdir)
    identity_writer.register_provisional(orchdir, ROOT, 1, model="claude-opus-4-8[1m]")
    c = _conn(orchdir)
    row = c.execute("SELECT model FROM generations WHERE root='a1' AND generation=1").fetchone()
    c.close()
    assert row[0] == "m", "must NOT re-stamp the canonical generation's model"
