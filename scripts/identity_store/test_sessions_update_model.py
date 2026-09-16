"""model_reconcile ADDENDUM (gm approved, msg /tmp/gm-g41-order.md step 3): the sanctioned
session writer must actually persist ``model`` and REFUSE an unknown model for an LLM seat.

Root cause OB found: identity_writer.update_session(fields={'model':X}) SILENTLY NO-OPPED
because shims.sessions_update's mutable set excluded 'model' (only conversation_path/note/
resume_command). model is typed-authoritative (projector composes the flat agent-sessions
model from generations.model; the doc carries no model field). Fix: add 'model' to the
mutable set (same txn as session_id) + a write-time assert that a claude/codex/gemini seat
never writes ''/'unknown' (services write 'n/a').
"""
import json
import os

import pytest

from scripts.identity_store import orchestra_db, projector, shims


def _seed(dbp, *, runtime="claude", model="unknown"):
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root,tier,runtime) VALUES ('seat','T2',?)", (runtime,))
    gid = c.execute("INSERT INTO generations (root,generation,session_id,model) "
                    "VALUES ('seat',1,'s1',?)", (model,)).lastrowid
    c.execute("INSERT INTO canonical (root,generation_id,tmux_session,status) "
              "VALUES ('seat',?,'seat','online')", (gid,))
    c.execute("INSERT INTO source_records (file,kind,key,ordinal,payload_json) "
              "VALUES ('agent-sessions.json','session','seat',0,?)",
              (json.dumps({"session_id": "s1", "generation": 1, "status": "online"}),))
    c.commit()
    return c, gid


def test_sessions_update_persists_model_typed(tmp_path):
    """model is typed-authoritative: sessions_update writes generations.model (was
    silently dropped). The strangler project() composes the flat model from that typed
    column (projector.py:105); the guard reads the typed column directly."""
    (tmp_path / "state").mkdir()
    dbp = os.path.join(tmp_path, "state", "orchestra-registry.db")
    c, gid = _seed(dbp)
    try:
        shims.sessions_update(c, "seat", {"model": "claude-opus-4-8[1m]"})
        assert c.execute("SELECT model FROM generations WHERE id=?", (gid,)
                         ).fetchone()["model"] == "claude-opus-4-8[1m]"
    finally:
        c.close()


@pytest.mark.parametrize("bad", ["", "unknown", None])
@pytest.mark.parametrize("rt", ["claude", "codex", "gemini"])
def test_sessions_update_refuses_unknown_model_for_llm(tmp_path, rt, bad):
    (tmp_path / "state").mkdir()
    dbp = os.path.join(tmp_path, "state", "orchestra-registry.db")
    c, _ = _seed(dbp, runtime=rt)
    try:
        with pytest.raises(shims.InvalidModel):
            shims.sessions_update(c, "seat", {"model": bad})
    finally:
        c.close()


def test_sessions_update_service_model_na_allowed(tmp_path):
    (tmp_path / "state").mkdir()
    dbp = os.path.join(tmp_path, "state", "orchestra-registry.db")
    c, gid = _seed(dbp, runtime="service")
    try:
        shims.sessions_update(c, "seat", {"model": "n/a"})
        assert c.execute("SELECT model FROM generations WHERE id=?", (gid,)
                         ).fetchone()["model"] == "n/a"
    finally:
        c.close()
