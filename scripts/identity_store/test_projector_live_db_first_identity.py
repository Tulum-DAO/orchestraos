"""RED (gm msg_816d4940, by effect 2026-09-16 13:05 Tulum): after identity_writer.swap_generation
moved task-gm canonical to gen 2 (sid 70b7024d) and project_faithful ran, registry.json still read
generation 1 / session_id None and agent-sessions.json still carried the OLD sid b43af7a4 — the
LIVE canonical row is served from the persisted document verbatim. Rule (mirror of the archive
rule c9560b947c): a live canonical row's session_id / generation / model / resume_command come
from the canonical GENERATION row (DB-first); the document only fills gaps; a NULL/'unknown'
row value never blanks a document value. Same rule in the registry and the sessions projection."""
import sys

sys.path.insert(0, ".")
from scripts.identity_store import projector  # noqa: E402

ROOT = "task-gm"
OLD_SID = "b43af7a4-2054-4a30-aada-174a517a84c3"
NEW_SID = "70b7024d-5355-44bb-b2bf-b89c73c21eb8"
RESUME2 = f"claude --resume {NEW_SID} --dangerously-skip-permissions"


def _snap(agent_doc, session_doc, *, gen2_model="claude-opus-4-8[1m]", gen2_resume=RESUME2):
    gens = [
        {"id": 67, "root": ROOT, "generation": 1, "session_id": OLD_SID, "model": "unknown",
         "resume_command": None, "retired_at": "2026-09-16T18:00:03Z"},
        {"id": 681, "root": ROOT, "generation": 2, "session_id": NEW_SID, "model": gen2_model,
         "resume_command": gen2_resume, "retired_at": None},
    ]
    docs = {"meta": {}, "agent": {}, "session": {}}
    if agent_doc is not None:
        docs["agent"][ROOT] = (0, agent_doc)
    if session_doc is not None:
        docs["session"][ROOT] = (0, session_doc)
    return {"docs": docs,
            "lineages": {ROOT: {"tier": "T2", "machine": "vps", "runtime": "claude",
                                "cwd": "/x", "always_on": 0}},
            "canonical": {ROOT: {"root": ROOT, "generation_id": 681, "tmux_session": ROOT,
                                 "status": "online"}},
            "generations": gens}


AGENT_DOC = {"name": ROOT, "lineage_root": ROOT, "generation": 1, "session_id": None,
             "resume_command": None, "model": "claude-opus-4-8[1m]", "runtime": "claude",
             "status": "online", "tmux_session": ROOT, "cwd": "/x"}   # keys PRESENT, values stale
SESSION_DOC = {"name": ROOT, "session_id": OLD_SID, "generation": 1, "status": "online",
               "resume_command": f"claude --resume {OLD_SID}"}


def test_registry_live_row_identity_is_db_first():
    row = projector._build_faithful_registry(_snap(AGENT_DOC, SESSION_DOC))["agents"][ROOT]
    assert row["generation"] == 2
    assert row["session_id"] == NEW_SID
    assert row["resume_command"] == RESUME2
    assert row["model"] == "claude-opus-4-8[1m]"
    assert row["runtime"] == "claude" and row["cwd"] == "/x" and row["status"] == "online"


def test_sessions_live_row_identity_is_db_first():
    row = projector._build_faithful_sessions(_snap(AGENT_DOC, SESSION_DOC))[ROOT]
    assert row["session_id"] == NEW_SID
    assert row["generation"] == 2
    assert row["resume_command"] == RESUME2
    assert row["status"] == "online"


def test_null_or_unknown_gen_values_never_blank_the_doc():
    snap = _snap(AGENT_DOC, SESSION_DOC, gen2_model="unknown", gen2_resume=None)
    snap["generations"][1]["session_id"] = None
    reg = projector._build_faithful_registry(snap)["agents"][ROOT]
    assert reg["model"] == "claude-opus-4-8[1m]"      # 'unknown' does not overwrite
    assert reg["generation"] == 2                     # generation still from the DB
    ses = projector._build_faithful_sessions(snap)[ROOT]
    assert ses["session_id"] == OLD_SID               # NULL row value keeps the doc's
    assert ses["resume_command"] == f"claude --resume {OLD_SID}"


def test_docless_canonical_root_stays_byte_faithful():
    """M1 zero-drift: the projection never ADDS a key the document never carried; a docless root
    stays the sparse {name, lineage_root} stub (a complete document is the writer's job —
    spawn_adopt persists one). Only keys PRESENT in the doc are corrected DB-first."""
    reg = projector._build_faithful_registry(_snap(None, None))["agents"][ROOT]
    assert reg == {"name": ROOT, "lineage_root": ROOT}
    doc = {"name": ROOT, "lineage_root": ROOT, "status": "online"}     # no identity keys
    reg = projector._build_faithful_registry(_snap(doc, None))["agents"][ROOT]
    assert "generation" not in reg and "session_id" not in reg


def test_archive_rule_unchanged():
    reg = projector._build_faithful_registry(_snap(AGENT_DOC, SESSION_DOC))["agents"]
    arch = reg[f"{ROOT}-gen1"]
    assert arch["session_id"] == OLD_SID and arch["status"] == "retired"
