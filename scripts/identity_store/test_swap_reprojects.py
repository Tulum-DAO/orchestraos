"""A swap re-projects the JSON views in the same step that moves the canonical pointer.

Live finding 2026-11-25: after a blue-green swap the data dir's registry.json kept the RETIRED
blue's session id until the next 15-minute reconciler run, because nothing on the swap path
projected. Readers of that file (the gateway's lineage stamp) treated the promoted seat as its
retired predecessor for up to 15 minutes. The swap now projects right after its commit; a
projection failure is logged and never turns a committed swap into a failed one.
"""
import json

import pytest

from scripts.identity_store import cutover, identity_writer, orchestra_db, projector, shims

ROOT = "orchestra-builder"


@pytest.fixture
def orch(tmp_path):
    (tmp_path / "state").mkdir()
    dbp = tmp_path / "state" / "orchestra-registry.db"
    orchestra_db.init_db(str(dbp))
    c = orchestra_db.get_connection(str(dbp))
    try:
        c.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)", (ROOT, "T2", "claude"))
        blue = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                         "VALUES (?,5,'blue-sid',?)", (ROOT, "m")).lastrowid
        c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
                  "VALUES (?,?,?,'online')", (ROOT, blue, ROOT))
        shims.upsert_document(c, "registry.json", "agent", ROOT, {
            "name": ROOT, "lineage_root": ROOT, "generation": 5, "session_id": "blue-sid",
            "model": "m", "runtime": "claude", "status": "online", "tmux_session": ROOT})
        c.commit()
    finally:
        c.close()
    cutover.arm(str(tmp_path))
    identity_writer.project_now(str(tmp_path))          # the views as they stood before the swap
    return tmp_path


def _registry_sid(orch):
    reg = json.loads((orch / "registry.json").read_text())
    agents = reg.get("agents") or {}
    return (agents.get(ROOT) or {}).get("session_id")


def _canonical_generation(orch):
    c = orchestra_db.get_connection(str(orch / "state" / "orchestra-registry.db"))
    try:
        return c.execute("SELECT g.generation FROM canonical k JOIN generations g "
                         "ON g.id = k.generation_id WHERE k.root=?", (ROOT,)).fetchone()[0]
    finally:
        c.close()


@pytest.mark.parametrize("sync", [False, True])
def test_swap_projects_the_new_session_into_registry_json(orch, sync):
    assert _registry_sid(orch) == "blue-sid"
    assert identity_writer.swap_generation(
        str(orch), ROOT, green={"generation": 6, "session_id": "green-sid", "model": "m"},
        sync_effects_owner=sync) is True
    assert _registry_sid(orch) == "green-sid", \
        "the swap must re-project in the same step, not wait for the next reconciler run"


def test_projection_failure_is_logged_and_the_swap_still_stands(orch, monkeypatch, capsys):
    def boom(conn, orchestra_dir):
        raise RuntimeError("projector exploded")
    monkeypatch.setattr(projector, "project_faithful", boom)
    assert identity_writer.swap_generation(
        str(orch), ROOT, green={"generation": 6, "session_id": "green-sid", "model": "m"}) is True
    assert _canonical_generation(orch) == 6                   # committed swap stands
    assert "projector exploded" in capsys.readouterr().err   # and the failure is said aloud
