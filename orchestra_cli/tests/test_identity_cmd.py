"""`orchestra identity arm` and init's arming of a new data dir (identity store armed by default).

A stock install used to ship with the cutover off: seats stayed `provisioning`, the session list
stayed `{}`, boot recovery resumed nothing and the reconciler refused to run (found on a fresh
VPS's reboot test). These run against the real identity_store code in a tmp data dir."""
import json
import sqlite3
from pathlib import Path

import pytest

from orchestra_cli import identity_cmd as IC
from orchestra_cli import init_cmd as I

REPO = Path(__file__).resolve().parents[2]


def _stock_data_dir(tmp_path, seats=None):
    """A data dir the way a stock install leaves it after a starter team: seats in registry.json,
    still `provisioning`, and an empty session list."""
    d = tmp_path / "data"
    (d / "state").mkdir(parents=True)
    agents = seats if seats is not None else {
        "gm": {"tier": "T0", "runtime": "claude", "status": "provisioning", "tmux_session": "gm"},
        "pm-first-project": {"tier": "T1", "runtime": "claude", "status": "provisioning",
                             "tmux_session": "pm-first-project", "reports_to": "gm"},
        "dev-first-project": {"tier": "T2", "runtime": "codex", "status": "provisioning",
                              "tmux_session": "dev-first-project", "reports_to": "pm-first-project"},
    }
    (d / "registry.json").write_text(json.dumps({"agents": agents}, indent=2))
    (d / "state" / "agent-sessions.json").write_text("{}\n")
    return d


def _rows(d, sql):
    conn = sqlite3.connect(str(d / "state" / "orchestra-registry.db"))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _data_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("IDENTITY_STORE_CUTOVER", raising=False)


def test_arm_imports_a_stock_team_and_flips_the_switch(tmp_path):
    d = _stock_data_dir(tmp_path)
    before = (d / "registry.json").read_text()
    rep = IC.arm(REPO, d, start_regenerator=False)
    assert rep["armed"] and rep["diffs"] == [] and rep["typed_populated"] == 3
    assert (d / "state" / "identity-store-cutover.flag").exists()
    assert (d / "state" / "identity-store-monitor.flag").exists()
    assert not IC.status(REPO, d)["frozen"]
    canon = {r["root"]: r["status"] for r in _rows(d, "SELECT root, status FROM canonical")}
    assert canon == {"gm": "provisioning", "pm-first-project": "provisioning", "dev-first-project": "provisioning"}
    # the registry's runtime is kept (a seat with no session row used to migrate as claude)
    runtimes = {r["root"]: r["runtime"] for r in _rows(d, "SELECT root, runtime FROM lineages")}
    assert runtimes["dev-first-project"] == "codex" and runtimes["gm"] == "claude"
    backup = Path(rep["backup"])
    assert (backup / "registry.json").read_text() == before
    assert json.loads((backup / "agent-sessions.json").read_text()) == {}
    assert (d / "registry.json").read_text() == before     # arming itself rewrites nothing readers see


def test_arm_twice_is_a_no_op(tmp_path):
    d = _stock_data_dir(tmp_path)
    IC.arm(REPO, d, start_regenerator=False)
    rep = IC.arm(REPO, d, start_regenerator=False)
    assert rep["already"] and rep["armed"]


def test_drift_refuses_and_leaves_the_box_unchanged(tmp_path, monkeypatch):
    d = _stock_data_dir(tmp_path)
    from scripts.identity_store import migrate
    monkeypatch.setattr(migrate, "diff_report", lambda a, b: ["registry.json: agents/gm/status differs"])
    with pytest.raises(IC.ArmError, match="round-trip"):
        IC.arm(REPO, d, start_regenerator=False)
    s = IC.status(REPO, d)
    assert s == {"cutover": False, "monitor": False, "frozen": False, "database": False, "identity_rows": False}


def test_a_leftover_database_with_rows_is_refused(tmp_path):
    d = _stock_data_dir(tmp_path)
    IC.arm(REPO, d, start_regenerator=False)
    (d / "state" / "identity-store-cutover.flag").unlink()      # a rolled-back arm
    with pytest.raises(IC.ArmError, match="already holds identity rows"):
        IC.arm(REPO, d, start_regenerator=False)
    assert not IC.status(REPO, d)["cutover"]


def test_dry_run_changes_nothing(tmp_path):
    d = _stock_data_dir(tmp_path)
    rep = IC.arm(REPO, d, dry_run=True)
    assert rep["dry_run"] and rep["typed_populated"] == 3 and rep["diffs"] == []
    assert IC.status(REPO, d) == {"cutover": False, "monitor": False, "frozen": False,
                                  "database": False, "identity_rows": False}
    assert not (d / "backups").exists()


def test_missing_flat_files_are_refused(tmp_path):
    d = tmp_path / "data"
    (d / "state").mkdir(parents=True)
    with pytest.raises(IC.ArmError, match="orchestra init"):
        IC.arm(REPO, d, start_regenerator=False)


def test_init_arms_a_new_data_dir_but_not_an_existing_one(tmp_path):
    d = _stock_data_dir(tmp_path, seats={})
    step = I._identity_step(REPO, d, fresh=True)
    assert step.did and "armed the identity store" in step.detail
    assert IC.status(REPO, d)["cutover"]
    assert I._identity_step(REPO, d, fresh=False).detail.startswith("armed")

    other = tmp_path / "old"
    (other / "state").mkdir(parents=True)
    (other / "registry.json").write_text('{"agents": {"gm": {"status": "online"}}}')
    (other / "state" / "agent-sessions.json").write_text("{}")
    step = I._identity_step(REPO, other, fresh=False)
    assert not step.did and "orchestra identity arm" in step.detail and "failed" not in step.detail
    assert not IC.status(REPO, other)["cutover"]


def test_local_machine_becomes_vps_so_recovery_and_the_reconciler_see_the_seat(tmp_path):
    seats = {"gm": {"tier": "T0", "runtime": "claude", "status": "provisioning", "machine": "local",
                    "tmux_session": "gm"},
             "far": {"tier": "T2", "runtime": "claude", "status": "online", "machine": "mac",
                     "tmux_session": "far"}}
    d = _stock_data_dir(tmp_path, seats=seats)
    dry = IC.arm(REPO, d, dry_run=True)
    assert dry["machine_normalized"] == ["gm"]
    assert json.loads((d / "registry.json").read_text())["agents"]["gm"]["machine"] == "local"   # dry run
    rep = IC.arm(REPO, d, start_regenerator=False)
    assert rep["machine_normalized"] == ["gm"] and rep["diffs"] == []
    agents = json.loads((d / "registry.json").read_text())["agents"]
    assert agents["gm"]["machine"] == "vps" and agents["far"]["machine"] == "mac"   # a real remote stays
    machines = {r["root"]: r["machine"] for r in _rows(d, "SELECT root, machine FROM lineages")}
    assert machines == {"gm": "vps", "far": "mac"}
    assert json.loads((Path(rep["backup"]) / "registry.json").read_text())["agents"]["gm"]["machine"] == "local"


def test_drift_after_normalizing_restores_the_original_registry(tmp_path, monkeypatch):
    d = _stock_data_dir(tmp_path, seats={"gm": {"status": "provisioning", "machine": "local"}})
    before = (d / "registry.json").read_text()
    from scripts.identity_store import migrate
    monkeypatch.setattr(migrate, "diff_report", lambda a, b: ["x"])
    with pytest.raises(IC.ArmError):
        IC.arm(REPO, d, start_regenerator=False)
    assert (d / "registry.json").read_text() == before


def _project(d):
    from scripts.identity_store import identity_writer
    identity_writer.project_now(str(d))


def test_a_flat_only_seat_is_erased_by_the_projection_once_armed(tmp_path):
    """The regression record_seat fixes: after arming, a row written only to registry.json
    disappears at the next projection (the regenerator runs one at least every 30 s)."""
    d = _stock_data_dir(tmp_path, seats={})
    IC.arm(REPO, d, start_regenerator=False)
    reg = json.loads((d / "registry.json").read_text())
    reg["agents"]["dev-new"] = {"tier": "T2", "runtime": "codex", "status": "provisioning"}
    (d / "registry.json").write_text(json.dumps(reg))
    _project(d)
    assert "dev-new" not in json.loads((d / "registry.json").read_text())["agents"]


def test_record_seat_survives_the_projection(tmp_path):
    d = _stock_data_dir(tmp_path, seats={})
    IC.arm(REPO, d, start_regenerator=False)
    row = {"tier": "T2", "runtime": "codex", "model": "gpt-5.6-terra", "status": "provisioning",
           "machine": "vps", "tmux_session": "dev-new", "reports_to": "pm-x"}
    reg = json.loads((d / "registry.json").read_text())
    reg["agents"]["dev-new"] = row
    (d / "registry.json").write_text(json.dumps(reg))
    assert IC.record_seat(REPO, d, "dev-new", row) is True
    _project(d)
    agents = json.loads((d / "registry.json").read_text())["agents"]
    assert agents["dev-new"]["runtime"] == "codex" and agents["dev-new"]["reports_to"] == "pm-x"
    lin = _rows(d, "SELECT runtime FROM lineages WHERE root='dev-new'")
    gen = _rows(d, "SELECT g.model FROM canonical c JOIN generations g ON g.id=c.generation_id WHERE c.root='dev-new'")
    assert lin == [{"runtime": "codex"}] and gen == [{"model": "gpt-5.6-terra"}]
    # a second write (orchestra agent's reports_to fix-up) updates the same seat
    row2 = dict(row, reports_to="pm-y")
    assert IC.record_seat(REPO, d, "dev-new", row2) is True
    _project(d)
    assert json.loads((d / "registry.json").read_text())["agents"]["dev-new"]["reports_to"] == "pm-y"
    assert len(_rows(d, "SELECT root FROM canonical WHERE root='dev-new'")) == 1


def test_record_seat_is_a_no_op_when_not_armed(tmp_path):
    d = _stock_data_dir(tmp_path, seats={})
    assert IC.record_seat(REPO, d, "x", {"status": "provisioning"}) is False
    assert not (d / "state" / "orchestra-registry.db").exists()


def test_demo_seats_survive_on_a_fresh_armed_init(tmp_path):
    d = _stock_data_dir(tmp_path, seats={})
    I._identity_step(REPO, d, fresh=True)                # init arms first ...
    added = I.seed_demo_registry(d / "registry.json")     # ... then --demo seeds
    assert added
    I._record_demo_seats(REPO, d, added)
    _project(d)
    assert set(added) <= set(json.loads((d / "registry.json").read_text())["agents"])
