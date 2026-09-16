"""item-1b (RED) — the FAITHFUL LIVE merge-projector (Part A, DEC-1788342210).

``project_faithful`` is the cutover daemon's projector. Unlike piece-3
``project()`` (a strangler shape over the typed subset), it must serve the LIVE
FULL document per identity so the large MUTABLE-UNMODELED field class (the crux of
the §0 enumeration — succeeded_by/resumable/last_active/the free-form state blob,
the sparse ``_canonical`` pin marker, …) is NEVER frozen at migration values.

Design under test (as ratified by the by-effect field enumeration):
  * each record's field VALUES come from its live ``source_records`` document
    (verbatim — nothing typed-frozen); writers keep it current in-txn (DP-A2);
  * the typed identity index decides only the projected SHAPE per operation:
      live canonical        -> under ``root``
      provisional successor -> alias  ``<root>-g<N>``  (Part B; registry-only)
      swap-retired pred.    -> archive ``<root>-gen<N>`` status=retired (KEPT)
      park-idle retire      -> REMOVED from registry.agents
  * meta keys served from their live meta documents; ``_provisional`` derived
    from live non-canonical generations (feasible from typed state; ties Part B).

RED until ``projector.project_faithful`` exists.
"""
import json
import os
from pathlib import Path

import pytest

from scripts.identity_store import migrate, orchestra_db, projector

_LIVE = Path(os.environ.get("ORCHESTRA_DIR",
                            os.path.expanduser("~/scripts/agent-orchestra")))


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------

@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "orchestra-registry.db"
    orchestra_db.init_db(str(p))
    c = orchestra_db.get_connection(str(p))
    yield c
    c.close()


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2))


def _synthetic_sources(src):
    """A small but STRUCTURALLY FAITHFUL live tree: rich per-agent docs with
    mutable-unmodeled fields, the sparse ``_canonical`` pin marker, an empty
    ``_provisional``, and a top-level ``_retired_agents`` dict."""
    registry = {
        "version": 1,
        "last_updated": "2026-09-02T00:00:00+00:00",
        "machines": {"vps": {"hostname": "srv", "primary_for": ["a1", "a2"]}},
        "agents": {
            "a1": {"name": "a1", "tier": "T2", "machine": "vps", "cwd": "/x",
                   "runtime": "claude", "model": "claude-opus-4-8[1m]",
                   "tmux_session": "a1", "always_on": True,
                   "system_prompt": "prompts/a1.md", "status": "online",
                   "generation": 3, "session_id": "s1", "lineage_root": "a1",
                   # mutable-unmodeled fields the store must NOT freeze:
                   "succeeded_by": None, "handoff_from": "a1(gen-2)",
                   "tags": ["p", "q"], "memory_scope": ["global"],
                   "sid_source": "observer"},
            "a2": {"name": "a2", "tier": "T1", "machine": "mac", "cwd": "/y",
                   "runtime": "gemini", "model": "gemini-3.1-pro",
                   "tmux_session": "a2", "always_on": False,
                   "system_prompt": "prompts/a2.md", "status": "quiescent",
                   "generation": 1, "session_id": "s2", "lineage_root": "a2",
                   "tags": [], "memory_scope": {"projects": ["z"]}},
        },
        "_retired_agents": {
            "old": {"name": "old", "tier": "T2", "retired_at": "t",
                    "lineage": ["old-g1"]},
        },
        "_provisional": {},
        "_canonical": {"a1": "x"},
    }
    sessions = {
        "a1": {"session_id": "s1", "model": "claude-opus-4-8[1m]", "generation": 3,
               "status": "online", "resumable": True, "last_active": "t-a1",
               "conversation_summary": "built things", "succeeded_by": None,
               "tmux_session": "a1"},
        "a2": {"session_id": "s2", "model": "gemini-3.1-pro", "generation": 1,
               "status": "quiescent", "resumable": True, "last_active": "t-a2",
               "tmux_session": "a2"},
    }
    _write_json(src / "registry.json", registry)
    _write_json(src / "state" / "agent-sessions.json", sessions)
    _write_json(src / "state" / "agents" / "a1.json",
                {"agent_id": "a1", "status": "online", "task": "building",
                 "tier": "T2", "blockers": [], "files_touched": ["x.py"],
                 "was_running_at_snapshot": True})
    _write_json(src / "state" / "agents" / "a2.json",
                {"agent_id": "a2", "status": "quiescent", "task": None,
                 "tier": "T1", "blockers": ["waiting on a1"]})
    return registry, sessions


def _migrate(conn, src):
    return migrate.migrate(
        conn,
        registry_path=str(src / "registry.json"),
        sessions_path=str(src / "state" / "agent-sessions.json"),
        agents_dir=str(src / "state" / "agents"))


def _persist_doc(conn, file, kind, key, payload):
    """Simulate a rewired writer persisting its FULL record into the live
    document store IN-TXN (DP-A2). Tests use this to represent the writer;
    item-1b under test is the PROJECTION of that live document."""
    conn.execute(
        "INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
        "VALUES (?, ?, ?, 0, ?) ON CONFLICT(file, kind, key) DO UPDATE SET "
        "payload_json=excluded.payload_json", (file, kind, key, json.dumps(payload)))


def _gen_id(conn, root, generation):
    return conn.execute("SELECT id FROM generations WHERE root=? AND generation=?",
                        (root, generation)).fetchone()["id"]


def _load(out_dir, rel):
    return json.loads((Path(out_dir) / rel).read_text())


# --------------------------------------------------------------------------
# M1 — flip-moment continuity (== migrate.reproject == live; runbook G2)
# --------------------------------------------------------------------------

def test_M1_flip_moment_zero_drift_synthetic(conn, tmp_path):
    src = tmp_path / "src"
    _synthetic_sources(src)
    _migrate(conn, src)
    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    diffs = migrate.diff_report(str(src), str(out))
    assert diffs == [], f"M1: faithful projection must be zero-drift vs live: {diffs[:12]}"


def test_M1_faithful_equals_reproject(conn, tmp_path):
    """M1 == runbook G2: at flip the faithful projector output equals
    migrate.reproject output byte-for-byte on the three files."""
    src = tmp_path / "src"
    _synthetic_sources(src)
    _migrate(conn, src)
    f_out, r_out = tmp_path / "faithful", tmp_path / "reproject"
    projector.project_faithful(conn, str(f_out))
    migrate.reproject(conn, str(r_out))
    for rel in ("registry.json", os.path.join("state", "agent-sessions.json")):
        assert _load(f_out, rel) == _load(r_out, rel), f"M1: {rel} drift vs reproject"


def test_M1_live_dataset_zero_drift(conn, tmp_path):
    reg = _LIVE / "registry.json"
    sess = _LIVE / "state" / "agent-sessions.json"
    agents = _LIVE / "state" / "agents"
    if not reg.exists() or not sess.exists():
        pytest.skip("live identity stores not present")
    import shutil
    src = tmp_path / "src"
    (src / "state" / "agents").mkdir(parents=True)
    shutil.copy(reg, src / "registry.json")
    shutil.copy(sess, src / "state" / "agent-sessions.json")
    if agents.is_dir():
        for f in agents.glob("*.json"):
            shutil.copy(f, src / "state" / "agents" / f.name)
    _migrate(conn, src)
    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    diffs = migrate.diff_report(str(src), str(out))
    assert diffs == [], f"M1 LIVE zero-drift violated ({len(diffs)} diffs): {diffs[:12]}"


# --------------------------------------------------------------------------
# M2 — swap reflected on X; every OTHER record byte-intact
# --------------------------------------------------------------------------

def test_M2_swap_reflected_others_byte_intact(conn, tmp_path):
    src = tmp_path / "src"
    _synthetic_sources(src)
    _migrate(conn, src)
    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    a2_before = _load(out, "state/agents/a2.json")
    reg_a2_before = _load(out, "registry.json")["agents"]["a2"]

    # promote a1 gen3 -> gen4 (typed swap) AND the rewired writer persists a1's
    # FULL new document (generation/session_id + a mutated succeeded_by).
    blue = _gen_id(conn, "a1", 3)
    orchestra_db.execute_swap(
        conn, "a1", green={"generation": 4, "session_id": "s1b",
                           "model": "claude-opus-4-8[1m]"},
        blue_generation_id=blue, now="t-swap")
    new_a1 = {**_load(out, "registry.json")["agents"]["a1"],
              "generation": 4, "session_id": "s1b", "succeeded_by": None}
    _persist_doc(conn, "registry.json", "agent", "a1", new_a1)
    _persist_doc(conn, "agent-sessions.json", "session", "a1",
                 {**_load(out, "registry.json")["agents"]["a1"],
                  "session_id": "s1b", "generation": 4})

    out2 = tmp_path / "faithful2"
    projector.project_faithful(conn, str(out2))
    reg2 = _load(out2, "registry.json")
    assert reg2["agents"]["a1"]["generation"] == 4
    assert reg2["agents"]["a1"]["session_id"] == "s1b"
    # a2 completely untouched by a1's swap:
    assert reg2["agents"]["a2"] == reg_a2_before, "M2: a2 registry record must be byte-intact"
    assert _load(out2, "state/agents/a2.json") == a2_before, "M2: a2 state blob must be byte-intact"


# --------------------------------------------------------------------------
# M3 — retire, SPLIT PER SHAPE
# --------------------------------------------------------------------------

def test_M3a_swap_retire_predecessor_kept_as_archive(conn, tmp_path):
    """After a promote swap on a1, the predecessor gen3 is KEPT, projected as
    ``a1-gen3`` status=retired with its sid preserved — NOT dropped; a1 (root)
    becomes the successor."""
    src = tmp_path / "src"
    _synthetic_sources(src)
    _migrate(conn, src)
    pred_doc = {"name": "a1", "tier": "T2", "generation": 3, "session_id": "s1",
                "status": "retired", "resumable": True, "lineage_root": "a1"}
    blue = _gen_id(conn, "a1", 3)
    orchestra_db.execute_swap(
        conn, "a1", green={"generation": 4, "session_id": "s1b",
                           "model": "claude-opus-4-8[1m]"},
        blue_generation_id=blue, now="t-swap")
    # rewired rotation writer: successor full doc under root + archive of predecessor.
    _persist_doc(conn, "registry.json", "agent", "a1",
                 {"name": "a1", "tier": "T2", "generation": 4, "session_id": "s1b",
                  "status": "online", "lineage_root": "a1"})
    _persist_doc(conn, "registry.json", "agent", "a1-gen3", pred_doc)

    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    reg = _load(out, "registry.json")
    assert "a1-gen3" in reg["agents"], "M3a: swap-retired predecessor must be KEPT as -gen<N> archive"
    assert reg["agents"]["a1-gen3"]["status"] == "retired"
    assert reg["agents"]["a1-gen3"]["session_id"] == "s1", "M3a: predecessor sid preserved"
    assert reg["agents"]["a1"]["generation"] == 4, "M3a: root is now the successor"


def test_M3b_park_idle_removed_from_registry(conn, tmp_path):
    """After a park-idle retire on a2 (canonical dropped), a2 is REMOVED from
    registry.agents; others byte-identical. A retired generation of a root with
    NO canonical row is NOT turned into an archive."""
    src = tmp_path / "src"
    _synthetic_sources(src)
    _migrate(conn, src)
    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    a1_before = _load(out, "registry.json")["agents"]["a1"]

    # park-idle DB effect: retire the generation + DROP the canonical pointer.
    gid = _gen_id(conn, "a2", 1)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE generations SET retired_at=? WHERE id=?", ("t-park", gid))
    conn.execute("DELETE FROM canonical WHERE root=?", ("a2",))
    conn.execute("COMMIT")

    out2 = tmp_path / "faithful2"
    projector.project_faithful(conn, str(out2))
    reg2 = _load(out2, "registry.json")
    assert "a2" not in reg2["agents"], "M3b: park-idle-retired agent must be REMOVED"
    assert "a2-gen1" not in reg2["agents"], "M3b: park-idle is REMOVE, not archive"
    assert reg2["agents"]["a1"] == a1_before, "M3b: other records byte-identical"


def test_M3c_retired_agents_dict_is_live_not_frozen(conn, tmp_path):
    """The top-level ``_retired_agents`` dict is served from its live meta
    document — a rewired full-retire writer updating it is reflected, proving the
    projector does not freeze it at the migration value."""
    src = tmp_path / "src"
    _synthetic_sources(src)
    _migrate(conn, src)
    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    assert set(_load(out, "registry.json")["_retired_agents"]) == {"old"}

    updated = {"old": {"name": "old", "tier": "T2", "retired_at": "t",
                       "lineage": ["old-g1"]},
               "a2": {"name": "a2", "tier": "T1", "retired_at": "t2",
                      "lineage": ["a2-g1"]}}
    _persist_doc(conn, "registry.json", "meta", "_retired_agents", updated)
    out2 = tmp_path / "faithful2"
    projector.project_faithful(conn, str(out2))
    assert set(_load(out2, "registry.json")["_retired_agents"]) == {"old", "a2"}, \
        "M3c: _retired_agents must be live, not frozen at migration"


# --------------------------------------------------------------------------
# M4 — EXHAUSTIVE MUTABLE-FIELD ENUMERATION GUARD (THE crux)
# --------------------------------------------------------------------------

# representative mutable-unmodeled field per writer surface (§0 enumeration):
_MUTABLE_CASES = [
    ("registry.json", "agent", "a1", "succeeded_by", "a1(gen-4)"),   # a promote field
    ("registry.json", "agent", "a1", "tags", ["p", "q", "r"]),        # mutable list
    ("agent-sessions.json", "session", "a1", "last_active", "t-updated"),
    ("agent-sessions.json", "session", "a1", "conversation_summary", "did more"),
]


@pytest.mark.parametrize("file,kind,key,field,newval", _MUTABLE_CASES)
def test_M4_mutable_unmodeled_field_is_carried_live(conn, tmp_path, file, kind,
                                                    key, field, newval):
    """For a representative mutable-unmodeled field of each writer surface: mutate
    it via the (simulated) rewired writer -> project_faithful reflects the NEW
    value, never the migration value. A field that can't be shown live is a
    schema/writer gap to close before trust."""
    src = tmp_path / "src"
    _synthetic_sources(src)
    _migrate(conn, src)
    # read the migrated document, mutate one field, persist (writer-in-txn):
    row = conn.execute(
        "SELECT payload_json FROM source_records WHERE file=? AND kind=? AND key=?",
        (file, kind, key)).fetchone()
    doc = json.loads(row["payload_json"])
    doc[field] = newval
    _persist_doc(conn, file, kind, key, doc)

    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    rel = "registry.json" if file == "registry.json" else "state/agent-sessions.json"
    projected = _load(out, rel)
    served = projected["agents"][key] if file == "registry.json" else projected[key]
    assert served[field] == newval, \
        f"M4: mutable-unmodeled field {field!r} was FROZEN, not served live"


def test_M4_state_blob_mutable_fields_carried(conn, tmp_path):
    """The free-form state/agents blob (state-snapshot's blockers/files_touched/
    was_running_at_snapshot etc.) is served whole from the live document."""
    src = tmp_path / "src"
    _synthetic_sources(src)
    _migrate(conn, src)
    doc = {"agent_id": "a1", "status": "online", "task": "shipping",
           "blockers": ["review pending"], "files_touched": ["y.py", "z.py"],
           "was_running_at_snapshot": True, "MERGE_9_PROGRESS": "step 3/5"}
    _persist_doc(conn, "state/agents", "state_agent", "a1.json", doc)
    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    assert _load(out, "state/agents/a1.json") == doc, \
        "M4: the free-form state blob must be served whole (no freezing)"


def test_M4_enumeration_guard_no_uncarried_mutable_bucket(conn, tmp_path):
    """The enumeration guard: classify every field across the projected records as
    modeled-typed | carried-via-live-document. Because project_faithful serves the
    WHOLE live document, the MUTABLE-UNMODELED-UNCARRIED bucket must be EMPTY."""
    src = tmp_path / "src"
    reg, sess = _synthetic_sources(src)
    _migrate(conn, src)
    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    proj_reg = _load(out, "registry.json")
    uncarried = []
    for name, doc in reg["agents"].items():
        pdoc = proj_reg["agents"].get(name)
        assert pdoc is not None, f"agent {name} missing from projection"
        for field, val in doc.items():
            if pdoc.get(field) != val:
                uncarried.append(f"registry.agents/{name}/{field}")
    assert uncarried == [], f"M4: fields not carried live (frozen/dropped): {uncarried}"


# --------------------------------------------------------------------------
# M5 — snapshot + atomic + liveness
# --------------------------------------------------------------------------

def test_M5_one_snapshot_id_in_sidecar_not_in_artifacts(conn, tmp_path):
    """U10: the triple comes from ONE read snapshot. Because the artifacts are
    BYTE-FAITHFUL (no injected header — else M1/G2 drift), the snapshot id lives
    in a SIDECAR the monitor reads, and never leaks into the three files."""
    src = tmp_path / "src"
    _synthetic_sources(src)
    _migrate(conn, src)
    out = tmp_path / "faithful"
    res = projector.project_faithful(conn, str(out))
    meta = _load(out, projector.FAITHFUL_META_NAME)
    assert meta["snapshot_id"] == res["snapshot_id"]
    # the artifacts stay header-free (byte-faithful):
    assert "_projection" not in _load(out, "registry.json")
    assert "_projection" not in _load(out, "state/agent-sessions.json")
    assert "_projection" not in _load(out, "state/agents/a1.json")
    # a fresh pass mints a new snapshot id:
    res2 = projector.project_faithful(conn, str(out))
    assert res2["snapshot_id"] != res["snapshot_id"]


def test_M5_atomic_write_leaves_no_temp_files(conn, tmp_path):
    src = tmp_path / "src"
    _synthetic_sources(src)
    _migrate(conn, src)
    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    projector.project_faithful(conn, str(out))
    leftover = [p.name for p in Path(out).rglob("*")
                if ".tmp" in p.name or p.name.endswith("~")]
    assert leftover == [], f"M5: atomic write must leave no temp files: {leftover}"


def test_M5_liveness_monitor_on_faithful_output(conn, tmp_path):
    src = tmp_path / "src"
    _synthetic_sources(src)
    _migrate(conn, src)
    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out), now=1000.0)
    alarms = []
    assert projector.check_faithful_liveness(str(out), now=1001.0, max_age_s=60,
                                             alarm=alarms.append) is True
    assert projector.check_faithful_liveness(str(out), now=2000.0, max_age_s=60,
                                             alarm=alarms.append) is False
    assert any(a["kind"] == "projector-stale" for a in alarms)


# --------------------------------------------------------------------------
# M6 — per-writer document-integrity (updating one field leaves others intact)
# --------------------------------------------------------------------------

def test_M6_single_field_update_leaves_doc_intact(conn, tmp_path):
    """Updating ONE field of an agent's record leaves every OTHER field of that
    record intact — atomicity by construction (source_records + typed tables share
    one SQLite DB; one txn covers index + document)."""
    src = tmp_path / "src"
    reg, _ = _synthetic_sources(src)
    _migrate(conn, src)
    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    a1_before = _load(out, "registry.json")["agents"]["a1"]

    doc = {**a1_before, "status": "quiescent"}
    _persist_doc(conn, "registry.json", "agent", "a1", doc)
    out2 = tmp_path / "faithful2"
    projector.project_faithful(conn, str(out2))
    a1_after = _load(out2, "registry.json")["agents"]["a1"]

    assert a1_after["status"] == "quiescent", "M6: the updated field changes"
    for k, v in a1_before.items():
        if k == "status":
            continue
        assert a1_after[k] == v, f"M6: field {k!r} must remain intact"
