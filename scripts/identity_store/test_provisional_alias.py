"""item-1b Part B (RED) — provisional-successor-alias representation (ob; no schema
change). DEC-1788342210.

A provisional successor is a plain NON-canonical ``generations`` row (canonical
stays on the predecessor). The faithful projector emits every LIVE, non-retired,
non-canonical generation as a REGISTRY-ONLY alias row ``<root>-g<N>`` in
``registry.agents`` with ``status=provisioning`` + the DP-B2 12-field payload, and
lists it in ``_provisional``. At swap the row becomes canonical under ``root`` and
the predecessor is KEPT as the DISTINCT ``<root>-gen<N>`` archive (DP-B1 — the two
conventions must not collapse).

RED until ``projector.project_faithful`` emits the alias shape.
"""
import json
import os
from pathlib import Path

import pytest

from scripts.identity_store import migrate, orchestra_db, projector

# DP-B2: the exact 12-field alias payload (enumerated from rotate_agent :475-489).
_ALIAS_FIELDS = {"name", "tier", "machine", "runtime", "model", "cwd",
                 "tmux_session", "generation", "lineage_root", "always_on",
                 "system_prompt", "status"}


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


def _sources(src):
    registry = {
        "version": 1, "last_updated": "t0",
        "machines": {"vps": {"hostname": "srv"}},
        "agents": {
            "a1": {"name": "a1", "tier": "T2", "machine": "vps", "cwd": "/x",
                   "runtime": "claude", "model": "claude-opus-4-8[1m]",
                   "tmux_session": "a1", "always_on": True,
                   "system_prompt": "prompts/a1.md", "status": "online",
                   "generation": 3, "session_id": "s1", "lineage_root": "a1"},
        },
        "_retired_agents": {}, "_provisional": {}, "_canonical": {"a1": "x"},
    }
    sessions = {"a1": {"session_id": "s1", "model": "claude-opus-4-8[1m]",
                       "generation": 3, "status": "online", "tmux_session": "a1"}}
    _write_json(src / "registry.json", registry)
    _write_json(src / "state" / "agent-sessions.json", sessions)
    _write_json(src / "state" / "agents" / "a1.json",
                {"agent_id": "a1", "status": "online", "task": "t"})


def _migrate(conn, src):
    migrate.migrate(conn, registry_path=str(src / "registry.json"),
                    sessions_path=str(src / "state" / "agent-sessions.json"),
                    agents_dir=str(src / "state" / "agents"))


def _add_provisional_gen(conn, root, generation, model="claude-opus-4-8[1m]"):
    """A provisional successor: a NON-canonical live generation row (canonical
    stays on the predecessor; session_id NULL until attributed)."""
    conn.execute(
        "INSERT INTO generations (root, generation, session_id, model, spawned_at) "
        "VALUES (?, ?, NULL, ?, 't-spawn')", (root, generation, model))


def _load(out_dir, rel):
    return json.loads((Path(out_dir) / rel).read_text())


def _gen_id(conn, root, generation):
    return conn.execute("SELECT id FROM generations WHERE root=? AND generation=?",
                        (root, generation)).fetchone()["id"]


# --------------------------------------------------------------------------
# P1 — non-canonical live gen projects as <root>-g<N>/provisioning (reg-only)
# --------------------------------------------------------------------------

def test_P1_provisional_gen_projects_as_alias(conn, tmp_path):
    src = tmp_path / "src"
    _sources(src)
    _migrate(conn, src)
    _add_provisional_gen(conn, "a1", 4)
    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    reg = _load(out, "registry.json")
    assert "a1-g4" in reg["agents"], "P1: provisional gen must project as <root>-g<N>"
    alias = reg["agents"]["a1-g4"]
    assert alias["status"] == "provisioning"
    assert alias["generation"] == 4
    assert alias["lineage_root"] == "a1"
    assert alias["name"] == "a1-g4"
    assert alias["tmux_session"] == "a1-g4"
    # a1 (predecessor) stays canonical at gen3:
    assert reg["agents"]["a1"]["generation"] == 3


def test_P1_alias_listed_in_provisional_meta(conn, tmp_path):
    src = tmp_path / "src"
    _sources(src)
    _migrate(conn, src)
    _add_provisional_gen(conn, "a1", 4)
    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    prov = _load(out, "registry.json")["_provisional"]
    assert "a1-g4" in prov, "P1: alias must appear in _provisional"


# --------------------------------------------------------------------------
# DP-B2 — exact 12-field payload
# --------------------------------------------------------------------------

def test_DPB2_alias_payload_is_exactly_twelve_fields(conn, tmp_path):
    src = tmp_path / "src"
    _sources(src)
    _migrate(conn, src)
    _add_provisional_gen(conn, "a1", 4)
    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    alias = _load(out, "registry.json")["agents"]["a1-g4"]
    assert set(alias) == _ALIAS_FIELDS, (
        f"DP-B2: alias payload must be exactly the 12 enumerated fields; "
        f"got extra={set(alias)-_ALIAS_FIELDS} missing={_ALIAS_FIELDS-set(alias)}")
    assert "session_id" not in alias, "DP-B2: NO session_id (spawned later)"
    # reproduced from the provisional generation row + its lineage:
    assert alias["tier"] == "T2"
    assert alias["runtime"] == "claude"
    assert alias["machine"] == "vps"
    assert alias["cwd"] == "/x"
    assert alias["always_on"] is True
    assert alias["model"] == "claude-opus-4-8[1m]"
    assert alias["system_prompt"] == "prompts/a1.md"


# --------------------------------------------------------------------------
# P4 — provisional alias is REGISTRY-ONLY (no sessions row)
# --------------------------------------------------------------------------

def test_P4_alias_is_registry_only_no_sessions_entry(conn, tmp_path):
    src = tmp_path / "src"
    _sources(src)
    _migrate(conn, src)
    _add_provisional_gen(conn, "a1", 4)
    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    sessions = _load(out, "state/agent-sessions.json")
    assert "a1-g4" not in sessions, "P4: provisional alias must NOT appear in agent-sessions.json"
    assert "a1-g4" in _load(out, "registry.json")["agents"], "P4: but IS in registry.agents"


# --------------------------------------------------------------------------
# P2 — at swap: alias -> canonical under root; predecessor KEPT as -gen<N>
# --------------------------------------------------------------------------

def test_P2_swap_promotes_alias_and_keeps_predecessor_archive(conn, tmp_path):
    src = tmp_path / "src"
    _sources(src)
    _migrate(conn, src)
    _add_provisional_gen(conn, "a1", 4)

    # promote: gen4 becomes canonical; predecessor gen3 retired + KEPT as archive.
    blue = _gen_id(conn, "a1", 3)
    orchestra_db.execute_swap(
        conn, "a1", green={"generation": 4, "session_id": "s1b",
                           "model": "claude-opus-4-8[1m]"},
        blue_generation_id=blue, now="t-swap")
    # rewired rotation writer: successor full doc under root + archive of predecessor.
    conn.execute(
        "INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
        "VALUES ('registry.json','agent','a1',0,?) ON CONFLICT(file,kind,key) "
        "DO UPDATE SET payload_json=excluded.payload_json",
        (json.dumps({"name": "a1", "tier": "T2", "machine": "vps", "cwd": "/x",
                     "runtime": "claude", "model": "claude-opus-4-8[1m]",
                     "tmux_session": "a1", "always_on": True,
                     "system_prompt": "prompts/a1.md", "status": "online",
                     "generation": 4, "session_id": "s1b", "lineage_root": "a1"}),))
    conn.execute(
        "INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
        "VALUES ('registry.json','agent','a1-gen3',0,?) ON CONFLICT(file,kind,key) "
        "DO UPDATE SET payload_json=excluded.payload_json",
        (json.dumps({"name": "a1", "tier": "T2", "generation": 3,
                     "session_id": "s1", "status": "retired", "resumable": True,
                     "lineage_root": "a1"}),))

    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    reg = _load(out, "registry.json")
    assert reg["agents"]["a1"]["generation"] == 4, "P2: successor canonical under root"
    assert "a1-g4" not in reg["agents"], "P2: alias no longer emitted once canonical"
    assert "a1-gen3" in reg["agents"], "P2/DP-B1: predecessor KEPT as -gen<N> archive"
    assert reg["agents"]["a1-gen3"]["status"] == "retired"


# --------------------------------------------------------------------------
# DP-B1 — the two conventions coexist and are DISTINCT
# --------------------------------------------------------------------------

def test_DPB1_provisional_and_archive_conventions_distinct(conn, tmp_path):
    """A live provisional successor (-g<N>) and a retired predecessor archive
    (-gen<N>) of the SAME root coexist as distinct rows (gm-g35 vs gm-gen34)."""
    src = tmp_path / "src"
    _sources(src)
    _migrate(conn, src)
    # a retired predecessor gen2 archive already present + a live provisional gen4:
    conn.execute("INSERT INTO generations (root, generation, model, retired_at) "
                 "VALUES ('a1', 2, 'm', 't-old')")
    conn.execute(
        "INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
        "VALUES ('registry.json','agent','a1-gen2',0,?)",
        (json.dumps({"name": "a1", "generation": 2, "status": "retired",
                     "session_id": "s0", "lineage_root": "a1"}),))
    _add_provisional_gen(conn, "a1", 4)

    out = tmp_path / "faithful"
    projector.project_faithful(conn, str(out))
    reg = _load(out, "registry.json")
    assert "a1-g4" in reg["agents"], "DP-B1: provisional alias present"
    assert "a1-gen2" in reg["agents"], "DP-B1: archive present"
    assert reg["agents"]["a1-g4"]["status"] == "provisioning"
    assert reg["agents"]["a1-gen2"]["status"] == "retired"
