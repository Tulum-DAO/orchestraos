"""Writer-resume p3 (RED) — swap-path DP-A2 full-record persistence.

A promote swap (promote_successor / rotate_agent Step-7) must, under cutover,
persist the FULL records the 3-store promote would have written — the successor
document under ``root``, the successor session, the state blob, AND the predecessor
``<root>-gen<N>`` archive — IN THE SAME TXN as the canonical Blue->Green repoint, so
project_faithful serves the successor under ``root`` and KEEPS the predecessor as the
retired archive (M3a). RIDER-2 (promote-then-rename) choreography + the
fail-closed-if-no-generation-row precondition are preserved.

RED until execute_swap/swap_generation accept + atomically persist ``documents``.
"""
import json
import os

import pytest

from scripts.identity_store import (cutover, identity_writer, orchestra_db,
                                     projector)

ROOT = "a1"


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    return str(tmp_path)


def _seed(orchdir):
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root, tier, runtime, machine, cwd) "
              "VALUES ('a1','T2','claude','vps','/x')")
    gid = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                    "VALUES ('a1',1,'s1','m')").lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES ('a1',?,'a1','online')", (gid,))
    c.execute("INSERT INTO runtime_state (generation_id, status, last_updated) "
              "VALUES (?,'online','t0')", (gid,))
    for file, kind, key, doc in [
        ("registry.json", "agent", "a1",
         {"name": "a1", "status": "online", "generation": 1, "session_id": "s1"}),
        ("agent-sessions.json", "session", "a1",
         {"session_id": "s1", "generation": 1, "status": "online"}),
    ]:
        c.execute("INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
                  "VALUES (?,?,?,0,?)", (file, kind, key, json.dumps(doc)))
    c.close()
    return gid


def _conn(orchdir):
    return orchestra_db.get_connection(
        os.path.join(orchdir, "state", "orchestra-registry.db"))


def _doc(orchdir, file, kind, key):
    c = _conn(orchdir)
    row = c.execute("SELECT payload_json FROM source_records "
                    "WHERE file=? AND kind=? AND key=?", (file, kind, key)).fetchone()
    c.close()
    return json.loads(row["payload_json"]) if row else None


# --- execute_swap documents are atomic with the typed repoint ---------------

def test_execute_swap_persists_documents_in_txn(orchdir):
    gid = _seed(orchdir)
    c = _conn(orchdir)
    docs = [("registry.json", "agent", ROOT,
             {"name": ROOT, "generation": 2, "session_id": "s2", "status": "online"}),
            ("registry.json", "agent", "a1-gen1",
             {"name": "a1", "generation": 1, "session_id": "s1", "status": "retired"})]
    orchestra_db.execute_swap(
        c, ROOT, green={"generation": 2, "session_id": "s2", "model": "m"},
        blue_generation_id=gid, now="t1", documents=docs)
    c.close()
    assert _doc(orchdir, "registry.json", "agent", ROOT)["generation"] == 2
    assert _doc(orchdir, "registry.json", "agent", "a1-gen1")["status"] == "retired"


def test_execute_swap_documents_rolled_back_on_failure(orchdir):
    gid = _seed(orchdir)
    c = _conn(orchdir)
    docs = [("registry.json", "agent", "a1-gen1", {"status": "retired"})]
    with pytest.raises(RuntimeError):
        orchestra_db.execute_swap(
            c, ROOT, green={"generation": 2, "session_id": "s2", "model": "m"},
            blue_generation_id=gid, now="t1", documents=docs, _fail_after=5)
    c.close()
    # the whole txn rolled back: neither the repoint nor the documents landed
    assert _doc(orchdir, "registry.json", "agent", "a1-gen1") is None
    c = _conn(orchdir)
    assert c.execute("SELECT generation_id FROM canonical WHERE root=?",
                     (ROOT,)).fetchone()["generation_id"] == gid
    c.close()


# --- swap_generation threads documents + INERT + fail-closed ----------------

def test_swap_generation_documents_reflected_by_project_faithful(orchdir, tmp_path):
    _seed(orchdir)
    cutover.arm(orchdir)
    succ = {"name": ROOT, "status": "online", "generation": 2, "session_id": "s2",
            "tier": "T2", "lineage_root": ROOT}
    succ_sess = {"session_id": "s2", "generation": 2, "status": "online"}
    archive = {"name": "a1", "generation": 1, "session_id": "s1",
               "status": "retired", "resumable": True, "lineage_root": ROOT}
    docs = [("registry.json", "agent", ROOT, succ),
            ("agent-sessions.json", "session", ROOT, succ_sess),
            ("registry.json", "agent", "a1-gen1", archive)]
    assert identity_writer.swap_generation(
        orchdir, ROOT, green={"generation": 2, "session_id": "s2", "model": "m"},
        blue_generation_id=None, now="t1", documents=docs) is True

    c = _conn(orchdir)
    out = tmp_path / "faithful"
    projector.project_faithful(c, str(out))
    c.close()
    reg = json.loads((out / "registry.json").read_text())
    sess = json.loads((out / "state" / "agent-sessions.json").read_text())
    assert reg["agents"][ROOT]["generation"] == 2, "successor canonical under root"
    assert reg["agents"][ROOT]["session_id"] == "s2"
    assert "a1-gen1" in reg["agents"], "predecessor KEPT as -gen<N> archive (M3a)"
    # item (a) (gm msg_9d5251c5): swap_generation resolves blue from canonical (gen1) and
    # the swap backfills its resume_command (claude/s1 -> resumable), so the resumable-
    # archive rule (item b) emits 'parked' — a resumable predecessor archive IS parked.
    assert reg["agents"]["a1-gen1"]["status"] == "parked"
    assert sess[ROOT]["session_id"] == "s2", "successor session under root"


def test_swap_generation_inactive_ignores_documents(orchdir):
    gid = _seed(orchdir)   # flag OFF
    before = _doc(orchdir, "registry.json", "agent", ROOT)
    docs = [("registry.json", "agent", "a1-gen1", {"status": "retired"})]
    assert identity_writer.swap_generation(
        orchdir, ROOT, green={"generation": 2, "session_id": "s2", "model": "m"},
        blue_generation_id=gid, documents=docs) is False
    assert _doc(orchdir, "registry.json", "agent", ROOT) == before
    assert _doc(orchdir, "registry.json", "agent", "a1-gen1") is None


def test_promote_successor_db_swap_persists_successor_and_archive(orchdir, tmp_path):
    """promote_successor._db_promote_swap under cutover persists the successor doc
    under root + the predecessor <root>-gen<N> archive (from the mutated reg/meta),
    so project_faithful shows successor-under-root + kept archive (M3a)."""
    import importlib.util
    _seed(orchdir)
    cutover.arm(orchdir)
    monkey_env = dict(os.environ)
    os.environ["ORCHESTRA_DIR"] = str(orchdir)
    os.environ.pop("IDENTITY_STORE_CUTOVER", None)
    try:
        promote_path = os.path.join(os.path.dirname(__file__), "..",
                                    "promote_successor.py")
        spec = importlib.util.spec_from_file_location("promote_p3", promote_path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)

        new_entry = {"name": ROOT, "generation": 2, "session_id": "s2",
                     "status": "online", "lineage_root": ROOT}
        sess_entry = {"session_id": "s2", "generation": 2, "model": "m",
                      "status": "online"}
        agent_state = {"agent_id": ROOT, "status": "online", "blockers": []}
        archive_key = "a1-gen1"
        reg = {"agents": {ROOT: new_entry, archive_key: {
            "name": "a1", "generation": 1, "session_id": "s1",
            "status": "retired", "lineage_root": ROOT}}}
        meta = {ROOT: sess_entry, archive_key: {
            "session_id": "s1", "generation": 1, "status": "retired"}}

        assert m._db_promote_swap(ROOT, new_entry, sess_entry,
                                  agent_state=agent_state, archive_key=archive_key,
                                  reg=reg, meta=meta) is True
    finally:
        os.environ.clear()
        os.environ.update(monkey_env)

    assert _doc(orchdir, "registry.json", "agent", archive_key)["status"] == "retired"
    c = _conn(orchdir)
    out = tmp_path / "faithful"
    projector.project_faithful(c, str(out))
    c.close()
    reg_out = json.loads((out / "registry.json").read_text())
    assert reg_out["agents"][ROOT]["generation"] == 2
    # item (a) (gm msg_9d5251c5): the swap backfills the retired blue's resume_command
    # (claude a1/s1 -> resumable), so the projector's resumable-archive rule (item b)
    # now emits 'parked', not 'retired' — a resumable predecessor archive IS parked.
    assert reg_out["agents"][archive_key]["status"] == "parked"
    assert _doc(orchdir, "state/agents", "state_agent", f"{ROOT}.json") == agent_state


def test_swap_generation_fail_closed_writes_no_documents(orchdir):
    """The fail-closed precondition (r-a-b) holds WITH documents: an unknown root
    raises and persists NOTHING (no identity written around the store)."""
    _seed(orchdir)
    cutover.arm(orchdir)
    docs = [("registry.json", "agent", "no-such-root-gen1", {"status": "retired"})]
    with pytest.raises(identity_writer.SwapPreconditionError):
        identity_writer.swap_generation(
            orchdir, "no-such-root",
            green={"generation": 2, "session_id": "sX", "model": "m"},
            blue_generation_id=None, documents=docs)
    assert _doc(orchdir, "registry.json", "agent", "no-such-root-gen1") is None
