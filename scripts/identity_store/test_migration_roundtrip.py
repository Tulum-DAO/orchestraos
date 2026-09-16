"""Piece-5 (RED) — U6: migration round-trip zero-drift on the LIVE dataset.

The final proof that the store is lossless: import the ACTUAL live
registry.json + agent-sessions.json + state/agents/*.json -> DB -> project back
-> byte-semantic diff = ZERO. This is where the piece-3 projection legacy-field
fidelity caveat RESOLVES: ``migrate.reproject`` reconstructs the files
byte-faithfully from the store.

Read-only import (copy into the harness; ob rider), tmp DB + tmp out_dir, nothing
mutates the live stores (the actual writer rewire + cutover is the the operator-armed
gate). A committed DIFF-REPORT (empty = proof) backs the the operator-arm card.

RED until ``scripts/identity_store/migrate`` exists.
"""
import json
import os
import shutil
from pathlib import Path

import pytest

from scripts.identity_store import orchestra_db, migrate

_LIVE = Path(os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2))


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "orchestra-registry.db"
    orchestra_db.init_db(str(p))
    c = orchestra_db.get_connection(str(p))
    yield c
    c.close()


# --- deterministic synthetic round-trip (hermetic) -------------------------

def _synthetic_sources(src):
    registry = {
        "version": 1,
        "last_updated": "2026-09-02T00:00:00+00:00",
        "machines": {"vps": {"hostname": "srv", "primary_for": ["a2", "a1"]}},
        "agents": {
            "a1": {"name": "a1", "tier": "T2", "machine": "vps", "cwd": "/x",
                   "tags": ["p", "q"], "system_prompt": "hi", "status": "online",
                   "always_on": True, "tmux_session": "a1"},
            "a2": {"name": "a2", "tier": "T1", "machine": "mac", "cwd": "/y",
                   "tags": [], "memory_scope": {"projects": ["z"]},
                   "status": "quiescent", "tmux_session": "a2"},
        },
        "_retired_agents": {
            "old": {"name": "old", "tier": "T2", "retired_at": "t", "lineage": ["old-g1"]},
        },
        "_provisional": {},
        "_canonical": {"a1": "x"},
    }
    sessions = {
        "a1": {"session_id": "s1", "model": "claude-opus-4-8[1m]", "generation": 3},
        "a2": {"session_id": "s2", "model": "gemini-3.1-pro", "generation": 1},
    }
    _write_json(src / "registry.json", registry)
    _write_json(src / "state" / "agent-sessions.json", sessions)
    _write_json(src / "state" / "agents" / "a1.json",
                {"agent_id": "a1", "status": "online", "task": "building", "tier": "T2"})
    _write_json(src / "state" / "agents" / "a2.json",
                {"agent_id": "a2", "status": "quiescent", "task": None, "tier": "T1"})


def test_synthetic_roundtrip_zero_drift(conn, tmp_path):
    src = tmp_path / "src"
    _synthetic_sources(src)
    migrate.migrate(conn,
                    registry_path=str(src / "registry.json"),
                    sessions_path=str(src / "state" / "agent-sessions.json"),
                    agents_dir=str(src / "state" / "agents"))
    out = tmp_path / "out"
    migrate.reproject(conn, str(out))
    diffs = migrate.diff_report(str(src), str(out))
    assert diffs == [], f"synthetic round-trip must be zero-drift: {diffs[:8]}"


def test_diff_report_ignores_non_json_placeholders(tmp_path):
    """G1 spot-fix (gm): diff_report must apply the SAME .json filter that
    migrate.py:144 uses to BOTH sides of the state/agents/ set comparison. A non-.json
    placeholder (.gitkeep, README) present in the ORIGINAL but absent from the
    projection (reproject only writes *.json) must NOT produce a spurious diff — else
    the real G1 blocker aborts on a 'state/agents/.gitkeep: missing in projection'."""
    src, out = tmp_path / "src", tmp_path / "out"
    for base in (src, out):
        _write_json(base / "registry.json", {"agents": {}})
        _write_json(base / "state" / "agent-sessions.json", {})
        (base / "state" / "agents").mkdir(parents=True, exist_ok=True)
    # identical agent .json on both sides (no drift there):
    _write_json(src / "state" / "agents" / "a1.json", {"agent_id": "a1", "status": "online"})
    _write_json(out / "state" / "agents" / "a1.json", {"agent_id": "a1", "status": "online"})
    # placeholders live in the ORIGINAL only (reproject never writes them):
    (src / "state" / "agents" / ".gitkeep").write_text("")
    (src / "state" / "agents" / "README.md").write_text("agent state dir\n")

    diffs = migrate.diff_report(str(src), str(out))
    assert diffs == [], f"non-.json placeholders must not diff: {diffs}"


def test_diff_report_still_flags_missing_agent_json(tmp_path):
    """Negative control: a genuinely MISSING agent .json (in original, not projection)
    must STILL diff — the filter must not blind diff_report to real agent loss."""
    src, out = tmp_path / "src", tmp_path / "out"
    for base in (src, out):
        _write_json(base / "registry.json", {"agents": {}})
        _write_json(base / "state" / "agent-sessions.json", {})
        (base / "state" / "agents").mkdir(parents=True, exist_ok=True)
    _write_json(src / "state" / "agents" / "a1.json", {"agent_id": "a1", "status": "online"})
    # a1.json is absent from the projection -> a real drift that MUST surface.
    diffs = migrate.diff_report(str(src), str(out))
    assert any("a1.json" in d and "missing in projection" in d for d in diffs), \
        f"a genuinely missing agent .json must still diff: {diffs}"


def test_typed_identity_tables_populated(conn, tmp_path):
    src = tmp_path / "src"
    _synthetic_sources(src)
    migrate.migrate(conn,
                    registry_path=str(src / "registry.json"),
                    sessions_path=str(src / "state" / "agent-sessions.json"),
                    agents_dir=str(src / "state" / "agents"))
    # the store's TYPED identity tables are populated from the live join, not
    # only the passthrough — a1/a2 lineages + their sessions land as rows.
    roots = {r["root"] for r in conn.execute("SELECT root FROM lineages").fetchall()}
    assert {"a1", "a2"} <= roots
    sids = {r["session_id"] for r in
            conn.execute("SELECT session_id FROM generations").fetchall()}
    assert {"s1", "s2"} <= sids


# --- the gm proof: the ACTUAL live dataset ---------------------------------

def test_live_dataset_roundtrip_zero_drift(conn, tmp_path):
    reg = _LIVE / "registry.json"
    sess = _LIVE / "state" / "agent-sessions.json"
    agents = _LIVE / "state" / "agents"
    if not reg.exists() or not sess.exists():
        pytest.skip("live identity stores not present")

    # copy the live sources into the harness (frozen baseline; zero live writes)
    src = tmp_path / "src"
    (src / "state" / "agents").mkdir(parents=True)
    shutil.copy(reg, src / "registry.json")
    shutil.copy(sess, src / "state" / "agent-sessions.json")
    if agents.is_dir():
        for f in agents.glob("*.json"):
            shutil.copy(f, src / "state" / "agents" / f.name)

    migrate.migrate(conn,
                    registry_path=str(src / "registry.json"),
                    sessions_path=str(src / "state" / "agent-sessions.json"),
                    agents_dir=str(src / "state" / "agents"))
    out = tmp_path / "out"
    migrate.reproject(conn, str(out))
    diffs = migrate.diff_report(str(src), str(out))
    assert diffs == [], f"LIVE zero-drift violated ({len(diffs)} diffs): {diffs[:8]}"
