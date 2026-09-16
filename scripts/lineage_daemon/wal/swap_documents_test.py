"""RED-first tests for build_swap_documents (stage-4, the load-bearing DP-A2 piece).

A swap must persist — IN the swap txn — the full records the legacy 3-store promote
would have written, so a fail-closed swap writes NOTHING and a succeeded swap leaves
the projection coherent. execute_swap consumes ``documents`` as a list of
``(file, kind, key, record)`` upserted to source_records (verified by effect:
orchestra_db.py:269-274). This builder mirrors promote_successor's computed
new_entry / sess_entry / agent_state for a gen{N}->gen{N+1} rotation.

Shape required (verified against test_swap_path_dpa2 + execute_swap):
  - successor doc: (registry.json, agent, <root>, green_registry_record)
  - successor session: (agent-sessions.json, session, <root>, green_session_record)
  - successor state: (state/agents, state_agent, <root>, green_state_record)
  - predecessor ARCHIVE: (registry.json, agent, <root>-gen<blue_gen>, blue_archive_record)
    — the archive lives under the distinct id <root>-gen<N> (NOT the canonical key),
    so the same-id-corpse class cannot recur.
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal.swap_documents import build_swap_documents  # noqa: E402


def _green(gen=3, sid="new-sid"):
    return {"generation": gen, "session_id": sid,
            "model": "claude-opus-4-8[1m]",
            "resume_command": f"claude --resume {sid} --dangerously-skip-permissions"}


def _blue_record():
    return {"name": "identity-store-builder", "generation": 2,
            "session_id": "0ed9c5d7", "status": "online", "tier": "T2"}


def _docs():
    return build_swap_documents(
        root="identity-store-builder", blue_generation=2,
        green=_green(3), blue_record=_blue_record(),
        cwd="/home/testuser/agent-orchestra")


def test_returns_list_of_4tuples():
    docs = _docs()
    assert all(len(t) == 4 for t in docs)
    for file, kind, key, record in docs:
        assert file in ("registry.json", "agent-sessions.json", "state/agents")
        assert kind in ("agent", "session", "state_agent")
        assert isinstance(record, dict)


def test_successor_doc_under_root_key():
    docs = _docs()
    succ = [(f, k, key, r) for (f, k, key, r) in docs
            if key == "identity-store-builder" and f == "registry.json"]
    assert len(succ) == 1
    rec = succ[0][3]
    assert rec["generation"] == 3           # the NEW generation
    assert rec["session_id"] == "new-sid"


def test_predecessor_archived_under_distinct_gen_key():
    docs = _docs()
    # archive key = <root>-gen<blue_gen>, NOT the canonical key (no same-id corpse)
    arch = [(f, k, key, r) for (f, k, key, r) in docs
            if key == "identity-store-builder-gen2"]
    assert len(arch) >= 1
    rec = arch[0][3]
    assert rec["generation"] == 2
    # archive is marked retired so it never reads as a live canonical
    assert rec.get("status") == "retired" or rec.get("retired_at")


def test_successor_session_record_present():
    docs = _docs()
    sess = [(f, k, key, r) for (f, k, key, r) in docs
            if f == "agent-sessions.json" and key == "identity-store-builder"]
    assert len(sess) == 1
    assert sess[0][3]["session_id"] == "new-sid"
    assert sess[0][3]["generation"] == 3


def test_archive_key_never_collides_with_canonical():
    docs = _docs()
    keys = [key for (_, _, key, _) in docs]
    # the successor (canonical key) and the archive (gen-key) are DISTINCT keys
    assert "identity-store-builder" in keys
    assert "identity-store-builder-gen2" in keys
    assert "identity-store-builder" != "identity-store-builder-gen2"


def test_no_session_id_leak_into_provisional_shape():
    # a provisional/successor session doc carries the green sid, never the blue's
    docs = _docs()
    for f, k, key, r in docs:
        if key == "identity-store-builder" and "session_id" in r:
            assert r["session_id"] == "new-sid"  # green, not blue 0ed9c5d7


# --- #15: per-runtime resume_command derivation for a provisional BG-capsule green ---
# A green promoted via the BG fire path carries NO resume_command (only the lineage
# rotate_agent path stamped one). build_swap_documents must DERIVE it from the seat's
# runtime + captured sid via the ONE builder (promote_successor.resume_command_for),
# so a promoted gemini seat is resumable as `agy --conversation <cid>` — not left
# resume_command=None (unresumable = the silent seat-corruption class), and never
# guessed as claude when the runtime is unknown.

def _succ(docs, root, file="registry.json"):
    return next(r for (f, k, key, r) in docs if f == file and key == root)


def test_gemini_green_without_resume_derives_agy_command():
    docs = build_swap_documents(
        root="demo-gemini-pred2", blue_generation=1,
        green={"generation": 2, "session_id": "7c211378", "model": "gemini-3.7-flash",
               "runtime": "gemini"},
        blue_record={"name": "demo-gemini-pred2", "generation": 1, "tier": "T2",
                     "runtime": "gemini"},
        cwd="/home/testuser/agent-orchestra")
    want = "agy --conversation 7c211378 --dangerously-skip-permissions"
    assert _succ(docs, "demo-gemini-pred2")["resume_command"] == want
    assert _succ(docs, "demo-gemini-pred2", "agent-sessions.json")["resume_command"] == want


def test_runtime_from_blue_record_when_green_lacks_it():
    # The green provisional often has no runtime field; the blue lineage runtime is
    # authoritative for a rotation (blue and green share the lineage).
    docs = build_swap_documents(
        root="demo-gemini-pred2", blue_generation=1,
        green={"generation": 2, "session_id": "sidX", "model": "gemini-3.7-flash"},
        blue_record={"name": "demo-gemini-pred2", "generation": 1, "tier": "T2",
                     "runtime": "gemini"},
        cwd="/x")
    assert _succ(docs, "demo-gemini-pred2")["resume_command"] == \
        "agy --conversation sidX --dangerously-skip-permissions"


def test_explicit_green_resume_still_wins_backcompat():
    # A green that already carries a resume_command keeps it verbatim (claude path).
    docs = _docs()  # uses _green(3) with an explicit claude resume_command
    assert _succ(docs, "identity-store-builder")["resume_command"] == \
        "claude --resume new-sid --dangerously-skip-permissions"


def test_missing_runtime_leaves_resume_absent_never_guesses_claude():
    # No resume_command AND no runtime anywhere -> REFUSE (leave absent), never default
    # to claude. A gemini seat resumed as claude is the exact silent-corruption defect.
    docs = build_swap_documents(
        root="s", blue_generation=1,
        green={"generation": 2, "session_id": "sidZ", "model": "m"},
        blue_record={"name": "s", "generation": 1, "tier": "T2"},  # no runtime
        cwd="/x")
    assert "resume_command" not in _succ(docs, "s")


def test_successor_carries_runtime_and_model_when_known():
    # The promoted seat's session doc must carry its resumable identity (runtime+model),
    # not just the sid — else read_ctx/idle-gate/the NEXT rotation see runtime=None.
    docs = build_swap_documents(
        root="demo-gemini-pred2", blue_generation=1,
        green={"generation": 2, "session_id": "7c211378", "model": "gemini-3.7-flash",
               "runtime": "gemini"},
        blue_record={"name": "demo-gemini-pred2", "generation": 1, "tier": "T2",
                     "runtime": "gemini"},
        cwd="/x")
    sess = _succ(docs, "demo-gemini-pred2", "agent-sessions.json")
    assert sess["runtime"] == "gemini"
    assert sess["model"] == "gemini-3.7-flash"
