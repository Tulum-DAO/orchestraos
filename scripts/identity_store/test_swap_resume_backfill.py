"""execute_swap resume-command completeness (gm msg_9d5251c5, from the first real-worker fire).
(a) the RETIRED blue gen gets resume_command backfilled (reap policy iii needs it in DB).
(b) a PROMOTED provisional green mirrors its resume_command onto the DB gen row."""
import os

from scripts.identity_store import orchestra_db


def _resume_for(runtime, sid):
    if runtime == "gemini":
        return f"agy --conversation {sid} --dangerously-skip-permissions"
    if runtime == "claude":
        return f"claude --resume {sid} --dangerously-skip-permissions"
    raise RuntimeError("non-resumable")


def _seed(dbp, runtime="gemini"):
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root,tier,runtime) VALUES ('seat','T2',?)", (runtime,))
    blue = c.execute("INSERT INTO generations (root,generation,session_id,model) "
                     "VALUES ('seat',1,'blue-sid','m')").lastrowid
    c.execute("INSERT INTO canonical (root,generation_id,tmux_session,status) "
              "VALUES ('seat',?,'seat','online')", (blue,))
    # a PROVISIONAL green already registered (session_id NULL) — the rotate/BG shape
    green = c.execute("INSERT INTO generations (root,generation,session_id,model) "
                      "VALUES ('seat',2,NULL,'m')").lastrowid
    c.commit()
    return c, blue, green


def test_blue_resume_backfilled_and_green_resume_mirrored(tmp_path):
    dbp = os.path.join(tmp_path, "orchestra-registry.db")
    c, blue, green = _seed(dbp)
    orchestra_db.execute_swap(
        c, "seat",
        {"generation": 2, "session_id": "green-sid", "model": "m",
         "resume_command": "agy --conversation green-sid --dangerously-skip-permissions"},
        blue_generation_id=blue, blue_resume_builder=_resume_for)
    # (a) retired blue now has a resume_command derived from its own runtime+sid
    brc = c.execute("SELECT resume_command FROM generations WHERE id=?", (blue,)
                    ).fetchone()["resume_command"]
    assert brc == "agy --conversation blue-sid --dangerously-skip-permissions"
    # (b) promoted green mirrors its resume_command onto the DB row
    grc = c.execute("SELECT resume_command FROM generations WHERE id=?", (green,)
                    ).fetchone()["resume_command"]
    assert grc == "agy --conversation green-sid --dangerously-skip-permissions"
    c.close()


def test_non_resumable_blue_left_null_never_guessed(tmp_path):
    dbp = os.path.join(tmp_path, "orchestra-registry.db")
    c, blue, _green = _seed(dbp, runtime="service")
    orchestra_db.execute_swap(
        c, "seat", {"generation": 2, "session_id": "g", "model": "m"},
        blue_generation_id=blue, blue_resume_builder=_resume_for)
    assert c.execute("SELECT resume_command FROM generations WHERE id=?", (blue,)
                     ).fetchone()["resume_command"] is None
    c.close()
