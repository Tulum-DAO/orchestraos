"""Piece-3 (RED) — projector core: U10 (one-snapshot triple) + U15 atomic write.

Strangler DP-U1(b): SQLite is the single write-truth; the projector regenerates
the legacy read artifacts (registry.json / agent-sessions.json /
state/agents/*.json) as READ-ONLY snapshots so ~130 unmodified legacy readers
keep working and can never see a torn state.

U10: all three projection kinds regenerate from ONE read-transaction snapshot,
stamped with a shared snapshot id — a legacy reader loading two projections from
different cycles must be impossible, else the torn read the DB kills is
reintroduced at the projection layer.

U15 (atomic): every projection file is written temp+rename (os.replace) so a
reader never observes a partial file; no temp files are left behind.

RED until ``scripts/identity_store/projector`` exists.
"""
import json

import pytest

from scripts.identity_store import orchestra_db, projector

ROOT = "orchestra-builder"


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "orchestra-registry.db"
    orchestra_db.init_db(str(p))
    c = orchestra_db.get_connection(str(p))
    yield c
    c.close()


def _seed_online_agent(conn, root=ROOT, generation=1, sid="sid-1",
                       model="model-x", tier="T2", runtime="claude"):
    conn.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)",
                 (root, tier, runtime))
    gid = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model) "
        "VALUES (?,?,?,?)", (root, generation, sid, model)).lastrowid
    conn.execute(
        "INSERT INTO canonical (root, generation_id, tmux_session, status) "
        "VALUES (?,?,?,'online')", (root, gid, root))
    conn.execute(
        "INSERT INTO runtime_state (generation_id, status, current_task, last_updated) "
        "VALUES (?,?,?,?)", (gid, "online", "building", "t0"))
    conn.commit()
    return gid


def _load(out_dir, rel):
    return json.loads((out_dir / rel).read_text())


def test_project_writes_three_projection_kinds(conn, tmp_path):
    _seed_online_agent(conn)
    out = tmp_path / "out"
    projector.project(conn, str(out))
    assert (out / "registry.json").exists()
    assert (out / "agent-sessions.json").exists()
    assert (out / "state" / "agents" / f"{ROOT}.json").exists()
    # all valid JSON
    _load(out, "registry.json")
    _load(out, "agent-sessions.json")
    _load(out, f"state/agents/{ROOT}.json")


def test_all_projections_share_one_snapshot_id(conn, tmp_path):
    _seed_online_agent(conn)
    out = tmp_path / "out"
    res = projector.project(conn, str(out))
    sid = res["snapshot_id"]
    reg = _load(out, "registry.json")["_projection"]["snapshot_id"]
    sess = _load(out, "agent-sessions.json")["_projection"]["snapshot_id"]
    agent = _load(out, f"state/agents/{ROOT}.json")["_projection"]["snapshot_id"]
    assert reg == sess == agent == sid, \
        "all projections in one pass must carry the SAME snapshot id (U10)"


def test_projection_reflects_db_identity(conn, tmp_path):
    _seed_online_agent(conn, sid="sid-1", model="model-x")
    out = tmp_path / "out"
    projector.project(conn, str(out))
    reg = _load(out, "registry.json")
    assert reg["agents"][ROOT]["session_id"] == "sid-1"
    assert reg["agents"][ROOT]["generation"] == 1
    sess = _load(out, "agent-sessions.json")
    assert sess[ROOT]["model"] == "model-x"
    agent = _load(out, f"state/agents/{ROOT}.json")
    assert agent["status"] == "online"
    assert agent["current_task"] == "building"


def test_atomic_write_leaves_no_temp_files(conn, tmp_path):
    _seed_online_agent(conn)
    out = tmp_path / "out"
    projector.project(conn, str(out))
    projector.project(conn, str(out))  # re-project overwrites cleanly
    leftover = [p.name for p in out.rglob("*") if ".tmp" in p.name or p.name.endswith("~")]
    assert leftover == [], f"atomic write must leave no temp files, found {leftover}"


def test_reproject_after_swap_updates_snapshot_and_content(conn, tmp_path):
    blue = _seed_online_agent(conn, generation=1, sid="sid-1", model="model-blue")
    out = tmp_path / "out"
    first = projector.project(conn, str(out))["snapshot_id"]
    orchestra_db.execute_swap(
        conn, ROOT,
        green={"generation": 2, "session_id": "sid-2", "model": "model-green"},
        blue_generation_id=blue, now="t1")
    second = projector.project(conn, str(out))["snapshot_id"]
    assert second != first, "each pass mints a fresh snapshot id"
    reg = _load(out, "registry.json")
    assert reg["agents"][ROOT]["generation"] == 2
    assert reg["agents"][ROOT]["session_id"] == "sid-2"
