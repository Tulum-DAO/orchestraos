"""RED (gm msg_e7d7f734 finding 2, by effect 2026-09-16 01:30Z): after the autonomous codex swap,
the flat registry projected `relational-intent-gen1` with session_id None / resume_command None
while the DB generation row holds 01a08c8c + `codex --yolo resume 01a08c8c` (same class as the
559b1512cc resume backfill, on the projection side). Cause: for a swap-retired archive the
projector copies the persisted `<root>-gen<N>` document VERBATIM (status aside), and the BG
swap path persisted that document from a flat doc that never carried the codex seat's sid.
Rule: identity fields on a swap-retired archive are DB-FIRST — session_id / resume_command
come from the typed generation row when it has them; the document only fills gaps. Applies
identically to the registry and the sessions projection."""
import sys

sys.path.insert(0, ".")
from scripts.identity_store import projector  # noqa: E402

ROOT = "relational-intent"
SID1 = "01a08c8c-c0ee-7e40-8e79-8ee7a56612f1"
RESUME1 = f"codex --yolo resume {SID1}"


def _snap(archive_doc, session_doc=None):
    gens = [
        {"id": 564, "root": ROOT, "generation": 1, "session_id": SID1,
         "resume_command": RESUME1, "retired_at": "2026-09-16T01:30:46Z", "model": "m"},
        {"id": 565, "root": ROOT, "generation": 2, "session_id": "01a0a4fe",
         "resume_command": "codex --yolo resume 01a0a4fe", "retired_at": None, "model": "m"},
    ]
    docs = {"meta": {},
            "agent": {ROOT: (0, {"name": ROOT, "lineage_root": ROOT, "session_id": "01a0a4fe"})},
            "session": {ROOT: (0, {"name": ROOT, "session_id": "01a0a4fe"})}}
    if archive_doc is not None:
        docs["agent"][f"{ROOT}-gen1"] = (1, archive_doc)
    if session_doc is not None:
        docs["session"][f"{ROOT}-gen1"] = (1, session_doc)
    return {"docs": docs, "lineages": {ROOT: {"tier": "T2", "machine": "vps",
                                             "runtime": "codex", "cwd": "/x", "always_on": 1}},
            "canonical": {ROOT: {"root": ROOT, "generation_id": 565, "tmux_session": ROOT,
                                 "status": "online"}},
            "generations": gens}


def test_registry_archive_identity_is_db_first_when_doc_lacks_it():
    doc = {"name": f"{ROOT}-gen1", "lineage_root": ROOT, "generation": 1, "runtime": "codex",
           "session_id": None, "resume_command": None, "status": "retired"}
    row = projector._build_faithful_registry(_snap(doc))["agents"][f"{ROOT}-gen1"]
    assert row["session_id"] == SID1
    assert row["resume_command"] == RESUME1
    assert row["status"] == "parked"          # resumable archive rule unchanged
    assert row["runtime"] == "codex"          # non-identity doc fields preserved


def test_registry_archive_doc_value_kept_when_gen_row_lacks_it():
    """DB-first never blanks a doc: a gen row without resume_command leaves the doc's."""
    doc = {"name": f"{ROOT}-gen1", "lineage_root": ROOT, "generation": 1,
           "session_id": SID1, "resume_command": "claude --resume x", "status": "parked"}
    snap = _snap(doc)
    snap["generations"][0]["resume_command"] = None
    row = projector._build_faithful_registry(snap)["agents"][f"{ROOT}-gen1"]
    assert row["resume_command"] == "claude --resume x"
    assert row["status"] == "retired"         # status still keyed on the GEN row (item b)


def test_sessions_archive_identity_is_db_first_too():
    sdoc = {"name": f"{ROOT}-gen1", "session_id": None, "resume_command": None,
            "status": "retired"}
    sessions = projector._build_faithful_sessions(_snap(None, sdoc))
    row = sessions[f"{ROOT}-gen1"]
    assert row["session_id"] == SID1 and row["resume_command"] == RESUME1
    assert row["status"] == "parked"


def test_synth_archive_without_doc_carries_db_identity():
    row = projector._build_faithful_registry(_snap(None))["agents"][f"{ROOT}-gen1"]
    assert row["session_id"] == SID1 and row["resume_command"] == RESUME1


# ---- sessions parity (gm msg_e7d7f734 residual / msg_347d7ee2 next): by effect 13 swap-retired
# archives under a live canonical have NO agent-sessions document (the BG swap path writes
# none; 9 have no persisted doc at all), so agent-sessions.json simply lacks the row while
# registry.json has one. Rule: the sessions projection SYNTHESIZES the archive row from the
# generation row when no session doc exists (mirror of the registry's _synth_archive), so
# the two files carry the same archive set with the same DB-first identity.

def test_sessions_archive_row_synthesized_when_no_session_doc():
    sessions = projector._build_faithful_sessions(_snap(None))     # no docs for gen1 at all
    row = sessions[f"{ROOT}-gen1"]
    assert row["session_id"] == SID1 and row["resume_command"] == RESUME1
    assert row["status"] == "parked" and row["generation"] == 1
    assert row["lineage_root"] == ROOT and row["runtime"] == "codex"


def test_sessions_synth_never_shadows_an_existing_session_doc():
    sdoc = {"name": f"{ROOT}-gen1", "session_id": SID1, "resume_command": RESUME1,
            "status": "parked", "tier": "T2", "custom": "kept"}
    sessions = projector._build_faithful_sessions(_snap(None, sdoc))
    assert sessions[f"{ROOT}-gen1"]["custom"] == "kept"


def test_sessions_synth_only_for_roots_with_a_canonical_pointer():
    snap = _snap(None)
    snap["canonical"] = {}                                             # park-idle full retire
    assert f"{ROOT}-gen1" not in projector._build_faithful_sessions(snap)
