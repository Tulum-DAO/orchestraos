"""session_doc_reconcile: bring a parked seat's session-DOC status to canonical.status
DB-first (gm msg_61b4581b). Dry-run reports; --apply writes the full doc + project_now."""
import json
import os

from scripts.identity_store import cutover, orchestra_db, session_doc_reconcile


def _seed(orchdir):
    (orchdir / "state").mkdir(parents=True, exist_ok=True)
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root,tier,runtime) VALUES ('seat','T2','claude')")
    gid = c.execute("INSERT INTO generations (root,generation,session_id,model) "
                    "VALUES ('seat',1,'s1','m')").lastrowid
    c.execute("INSERT INTO canonical (root,generation_id,tmux_session,status) "
              "VALUES ('seat',?,'seat','parked')", (gid,))
    # session doc carries a legacy 'quiescent' (item-a never touched it)
    c.execute("INSERT INTO source_records (file,kind,key,ordinal,payload_json) "
              "VALUES ('agent-sessions.json','session','seat',0,?)",
              (json.dumps({"session_id": "s1", "generation": 1, "status": "quiescent",
                           "resume_command": "claude --resume s1"}),))
    c.commit()
    c.close()
    return dbp


def test_dry_run_reports_no_write(tmp_path):
    _seed(tmp_path)
    rep = session_doc_reconcile.reconcile(str(tmp_path), apply=False)
    assert rep["changed"] == 1 and rep["by_old_status"] == {"quiescent": 1}
    assert rep["applied"] is False


def test_apply_sets_session_doc_status_and_preserves_fields(tmp_path):
    dbp = _seed(tmp_path)
    cutover.arm(str(tmp_path))
    rep = session_doc_reconcile.reconcile(str(tmp_path), apply=True)
    assert rep["changed"] == 1
    c = orchestra_db.get_connection(dbp)
    try:
        doc = json.loads(c.execute(
            "SELECT payload_json FROM source_records WHERE file='agent-sessions.json' "
            "AND kind='session' AND key='seat'").fetchone()["payload_json"])
    finally:
        c.close()
    assert doc["status"] == "parked"                       # reconciled
    assert doc["resume_command"] == "claude --resume s1"   # other fields preserved
    assert doc["session_id"] == "s1"


def test_never_writes_nonenum_status(tmp_path):
    """A canonical seat holding a (hypothetical) non-enum status is never copied onto
    the doc — the reconcile only propagates valid enum values."""
    dbp = _seed(tmp_path)
    c = orchestra_db.get_connection(dbp)
    # force a non-enum canonical.status directly (bypassing the gate, as legacy data could)
    c.execute("UPDATE canonical SET status='weird' WHERE root='seat'")
    c.commit(); c.close()
    rep = session_doc_reconcile.reconcile(str(tmp_path), apply=False)
    assert rep["changed"] == 0
