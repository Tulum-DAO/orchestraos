"""Writer-resume piece-1 (RED) — DP-A2: writers persist the FULL record in-txn.

item-1b's ``project_faithful`` serves each identity record's LIVE FULL DOCUMENT
from ``source_records``. That is only faithful if the rewired writers KEEP that
document current: every write must upsert the full record the writer would have
written to JSON into ``source_records`` IN THE SAME TRANSACTION as its typed-column
write (gm msg_23f94e56 — index and document never diverge). The piece-2 shims wrote
ONLY typed columns, so after a real write ``project_faithful`` would serve the STALE
migrated document. This piece closes that gap at the seam.

RED until the seam ops accept + atomically persist ``full_record``.
"""
import json
import os

import pytest

from scripts.identity_store import (cutover, identity_writer, migrate,
                                     orchestra_db, projector)

ROOT = "a1"


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    return str(tmp_path)


def _seed_migrated(orchdir):
    """Seed the store the way migration does: typed identity rows + a faithful
    source_records document per record (so a stale-vs-live comparison is real)."""
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root, tier, runtime, machine, cwd) "
              "VALUES (?,?,?,?,?)", (ROOT, "T2", "claude", "vps", "/x"))
    gid = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                    "VALUES (?,?,?,?)", (ROOT, 1, "s1", "m")).lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES (?,?,?,'online')", (ROOT, gid, ROOT))
    c.execute("INSERT INTO runtime_state (generation_id, status, last_updated) "
              "VALUES (?,'online','t0')", (gid,))
    # faithful documents (the migration values)
    for file, kind, key, doc in [
        ("registry.json", "agent", ROOT,
         {"name": ROOT, "tier": "T2", "status": "online", "generation": 1,
          "session_id": "s1", "succeeded_by": None, "tags": ["p"]}),
        ("agent-sessions.json", "session", ROOT,
         {"session_id": "s1", "generation": 1, "status": "online",
          "resumable": True, "last_active": "t0"}),
        ("state/agents", "state_agent", f"{ROOT}.json",
         {"agent_id": ROOT, "status": "online", "blockers": []}),
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


# --- INERT: flag off => no source_records write ----------------------------

def test_full_record_ignored_when_cutover_inactive(orchdir):
    _seed_migrated(orchdir)
    before = _doc(orchdir, "registry.json", "agent", ROOT)
    assert identity_writer.update_registry_agent(
        orchdir, ROOT, {"status": "parked"},
        full_record={"name": ROOT, "status": "parked"}) is False
    assert _doc(orchdir, "registry.json", "agent", ROOT) == before, \
        "INERT: flag-off must not touch source_records"


# --- registry agent: typed + full document in ONE op -----------------------

def test_update_registry_agent_persists_full_document(orchdir):
    _seed_migrated(orchdir)
    cutover.arm(orchdir)
    new_doc = {"name": ROOT, "tier": "T2", "status": "parked", "generation": 1,
               "session_id": "s1", "succeeded_by": "a1(gen-2)", "tags": ["p", "q"]}
    assert identity_writer.update_registry_agent(
        orchdir, ROOT, {"status": "parked"}, full_record=new_doc) is True
    # the live document reflects the FULL record (incl. mutable-unmodeled fields):
    assert _doc(orchdir, "registry.json", "agent", ROOT) == new_doc
    # and the typed index moved too (same op):
    c = orchestra_db.get_connection(
        os.path.join(orchdir, "state", "orchestra-registry.db"))
    assert c.execute("SELECT status FROM canonical WHERE root=?",
                     (ROOT,)).fetchone()["status"] == "parked"
    c.close()


def test_registry_write_reflected_by_project_faithful(orchdir, tmp_path):
    _seed_migrated(orchdir)
    cutover.arm(orchdir)
    new_doc = {"name": ROOT, "tier": "T2", "status": "parked", "generation": 1,
               "session_id": "s1", "succeeded_by": "a1(gen-2)", "tags": ["p", "q"]}
    identity_writer.update_registry_agent(
        orchdir, ROOT, {"status": "parked"}, full_record=new_doc)
    c = orchestra_db.get_connection(
        os.path.join(orchdir, "state", "orchestra-registry.db"))
    out = tmp_path / "faithful"
    projector.project_faithful(c, str(out))
    c.close()
    served = json.loads((out / "registry.json").read_text())["agents"][ROOT]
    assert served == new_doc, "project_faithful must serve the LIVE written document, not the stale migrated one"


# --- session ---------------------------------------------------------------

def test_update_session_persists_full_document(orchdir):
    _seed_migrated(orchdir)
    cutover.arm(orchdir)
    sdoc = {"session_id": "s1", "generation": 1, "status": "online",
            "resumable": True, "last_active": "t-NEW", "succeeded_by": "a1(gen-2)"}
    assert identity_writer.update_session(
        orchdir, ROOT, {"note": "x"}, full_record=sdoc) is True
    assert _doc(orchdir, "agent-sessions.json", "session", ROOT) == sdoc


# --- state blob ------------------------------------------------------------

def test_write_agent_state_persists_full_blob(orchdir):
    _seed_migrated(orchdir)
    cutover.arm(orchdir)
    blob = {"agent_id": ROOT, "status": "online", "blockers": ["review pending"],
            "files_touched": ["y.py"], "was_running_at_snapshot": True}
    assert identity_writer.write_agent_state(
        orchdir, ROOT, {"status": "online"}, full_record=blob) is True
    assert _doc(orchdir, "state/agents", "state_agent", f"{ROOT}.json") == blob


# --- retire: canonical drop + registry-doc REMOVED + retired session KEPT ---

def test_retire_agent_removes_registry_keeps_retired_session(orchdir):
    _seed_migrated(orchdir)
    cutover.arm(orchdir)
    retired_session = {"session_id": "s1", "generation": 1, "status": "retired",
                       "resumable": True, "resume_command": "resume a1",
                       "last_active": "t0"}
    assert identity_writer.retire_agent(
        orchdir, ROOT, reason="idle", session_record=retired_session) is True
    # registry source doc REMOVED (park-idle pops registry.agents):
    assert _doc(orchdir, "registry.json", "agent", ROOT) is None
    # retired session doc KEPT (resumable) — DP-A2 in-txn:
    assert _doc(orchdir, "agent-sessions.json", "session", ROOT) == retired_session
    c = orchestra_db.get_connection(
        os.path.join(orchdir, "state", "orchestra-registry.db"))
    # canonical dropped; generation preserved with retired_at:
    assert c.execute("SELECT 1 FROM canonical WHERE root=?", (ROOT,)).fetchone() is None
    assert c.execute("SELECT retired_at FROM generations WHERE root=?",
                     (ROOT,)).fetchone()["retired_at"] is not None
    c.close()


def test_retire_reflected_by_project_faithful(orchdir, tmp_path):
    """After a park-idle retire under cutover: project_faithful REMOVES the agent
    from registry.agents but KEEPS it retired in agent-sessions.json."""
    _seed_migrated(orchdir)
    cutover.arm(orchdir)
    retired_session = {"session_id": "s1", "generation": 1, "status": "retired",
                       "resumable": True, "resume_command": "resume a1"}
    identity_writer.retire_agent(orchdir, ROOT, reason="idle",
                                 session_record=retired_session)
    c = orchestra_db.get_connection(
        os.path.join(orchdir, "state", "orchestra-registry.db"))
    out = tmp_path / "faithful"
    projector.project_faithful(c, str(out))
    c.close()
    reg = json.loads((out / "registry.json").read_text())
    sess = json.loads((out / "state" / "agent-sessions.json").read_text())
    assert ROOT not in reg["agents"], "retired agent removed from registry.agents"
    assert sess.get(ROOT) == retired_session, "retired session KEPT (resumable)"


# --- backward compatibility: no full_record => typed-only (piece-2 behavior)

def test_no_full_record_leaves_document_untouched(orchdir):
    _seed_migrated(orchdir)
    cutover.arm(orchdir)
    before = _doc(orchdir, "registry.json", "agent", ROOT)
    assert identity_writer.update_registry_agent(
        orchdir, ROOT, {"status": "parked"}) is True   # no full_record
    assert _doc(orchdir, "registry.json", "agent", ROOT) == before, \
        "back-compat: without full_record the document is untouched (typed-only)"
