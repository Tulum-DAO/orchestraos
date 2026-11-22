"""A dashboard rename (PUT /api/agents/:id/name -> seat_names) is a name Arturo finds the agent by, everywhere it
looks one up: the agent tools, a menu answer, reports_to on a spawn, the list of agents and the name a call says.
The old id still works, a seat only registry.json knows still works, and a name nobody has is still not found.

A client install renamed its manager "general manager" and Arturo said it could not find "general manager". The
rename here is "Front Desk" on purpose: "general manager" is also a role phrase the spoken-name matcher maps to
"gm", which hides a lookup that never read the seat name at all."""
import importlib.util
import json
import pathlib

import pytest

from scripts.identity_store import orchestra_db as odb
from scripts.identity_store import seat_names as sn
from scripts.identity_store import seat_resolve as sr
from services.arturo import agent_call as AC

LIVE = {"gm", "pm-demo", "dev-demo"}


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("arturo_proxy_seat_lookup", pathlib.Path("services/arturo/arturo-proxy.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def install(tmp_path, monkeypatch, mod):
    """A data dir: gm and pm-demo in the identity DB and registry.json, gm renamed "Front Desk" from the dashboard;
    dev-demo in registry.json only (no DB row, never renamed)."""
    (tmp_path / "state").mkdir()
    (tmp_path / "registry.json").write_text(json.dumps({"agents": {
        "gm": {"tier": "T0"}, "pm-demo": {"tier": "T1", "reports_to": "gm"},
        "dev-demo": {"tier": "T2", "reports_to": "pm-demo"}}}))
    db = sn.db_path_for(tmp_path)
    odb.init_db(db)
    c = odb.get_connection(db)
    for root in ("gm", "pm-demo"):
        c.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?, 'T1', 'claude')", (root,))
    sn.set_name(c, "gm", "Front Desk", by="dashboard")
    c.close()
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "ORCHESTRA_DIR", tmp_path)
    monkeypatch.setattr(sr, "_live_tmux_sessions", lambda: set(LIVE))
    monkeypatch.setattr(sr, "_pane_has_claude", lambda t: True)
    monkeypatch.setattr(mod, "_live_seats", lambda: sorted(LIVE))
    return tmp_path


@pytest.fixture
def run(mod, install, monkeypatch):
    """A tool through Arturo's front door (_execute_tool_impl) up to the point it would act: (result, args it ran with)."""
    seen = []
    monkeypatch.setattr(mod, "_execute_tool_raw", lambda name, args, user_turns=None: seen.append(dict(args)) or f"ran {name}")
    monkeypatch.setattr(mod, "_watch_agent_work", lambda name, args, out: out)

    def go(name, **args):
        seen.clear()
        return mod._execute_tool_impl(name, args), (seen[-1] if seen else None)
    return go


def test_a_renamed_seat_is_found_by_its_new_name(run, mod, install):
    out, args = run("agent_message", to_agent="Front Desk", subject="status")
    assert args["to_agent"] == "gm"
    out, args = run("inject_message", session_name="front desk", message="hi")
    assert args["session_name"] == "gm"
    out, args = run("answer_menu", session="Front Desk", option="1")
    assert args["session"] == "gm"
    assert mod._asked_parent("Front Desk", "dev-new") == "gm"
    assert AC.display_name("gm", install) == "Front Desk"
    rows = mod._registry_hierarchy()
    text = mod._list_agents_text(rows, LIVE, set(), detect=lambda n: "idle", names=mod._seat_names())
    assert "gm (Front Desk): T0" in text


def test_the_old_id_still_works(run, mod):
    out, args = run("agent_message", to_agent="gm", subject="status")
    assert args["to_agent"] == "gm"
    out, args = run("answer_menu", session="gm", option="1")
    assert args["session"] == "gm"
    assert mod._asked_parent("gm", "dev-new") == "gm"


def test_a_seat_only_registry_json_knows_still_works(run, mod, install):
    out, args = run("agent_message", to_agent="dev-demo", subject="status")
    assert args["to_agent"] == "dev-demo"
    out, args = run("answer_menu", session="dev-demo", option="2")
    assert args["session"] == "dev-demo"
    assert mod._asked_parent("pm demo", "dev-new") == "pm-demo"          # the id's words, as before
    assert mod._asked_parent("dev-demo", "dev-new") == "dev-demo"
    assert AC.display_name("dev-demo", install) == "dev-demo"
    text = mod._list_agents_text(mod._registry_hierarchy(), LIVE, set(), detect=lambda n: "idle", names=mod._seat_names())
    assert "  dev-demo: T2" in text and "  pm-demo: T1" in text


def test_an_unknown_name_is_still_not_found(run, mod):
    out, args = run("agent_message", to_agent="Back Office", subject="status")
    assert out.startswith("NO SUCH AGENT") and args is None
    out, args = run("answer_menu", session="Back Office", option="1")
    assert out.startswith("NO SUCH AGENT") and args is None
    refused = mod._asked_parent("Back Office", "dev-new")
    assert refused.startswith("NOT RUN") and "gm (Front Desk)" in refused     # the names it could have used
    assert mod._asked_parent("Front Desk", "gm").startswith("NOT RUN")        # never itself


def test_no_identity_store_falls_back_to_the_registry(mod, tmp_path, monkeypatch):
    (tmp_path / "registry.json").write_text(json.dumps({"agents": {"gm": {"tier": "T0", "display_name": "Boss"}}}))
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setattr(sr, "_live_tmux_sessions", lambda: {"gm"})
    monkeypatch.setattr(sr, "_pane_has_claude", lambda t: True)
    assert AC.display_name("gm", tmp_path) == "Boss"
    assert mod._asked_parent("gm", "dev-new", {"agents": {"gm": {}}}) == "gm"
    assert mod._seat_names() == {}


def test_list_agents_shows_names_without_scanning_every_pane(mod, install, monkeypatch):
    """The names come from seat_names alone: list_agents never loads the directory (a pane check per live seat,
    about 9 s on a fleet of 78 live seats) just to read them."""
    import sys
    import types

    def no_scan(*a, **k):
        raise AssertionError("list_agents loaded the whole agent directory to read names")
    monkeypatch.setattr(sr, "load_directory", no_scan)
    monkeypatch.setattr(mod, "_seat_directory", no_scan)
    monkeypatch.setattr(mod, "run_local", lambda cmd, timeout=5: (True, "\n".join(sorted(LIVE))))
    monkeypatch.setattr(mod, "_detector_state", lambda n: "idle")
    fake = types.ModuleType("msg_store")
    fake.MessageStore = lambda: types.SimpleNamespace(hierarchy_list=lambda tenant_id=None: [])
    monkeypatch.setitem(sys.modules, "msg_store", fake)
    out = mod._execute_tool_raw("list_agents", {})
    assert "gm (Front Desk): T0" in out and "  dev-demo: T2" in out


def test_an_exact_id_wins_over_another_agents_name_with_the_same_letters(run, mod, install):
    """pm-demo was renamed "Ops"; a seat with the id "ops" was made later (creation never checks names). The card
    says "ops", so answer_menu(session="ops") is the seat ops, not "could mean more than one"."""
    c = odb.get_connection(sn.db_path_for(install))
    sn.set_name(c, "pm-demo", "Ops", by="dashboard")
    c.execute("INSERT INTO lineages (root, tier, runtime) VALUES ('ops', 'T2', 'claude')")
    c.close()
    reg = json.loads((install / "registry.json").read_text())
    reg["agents"]["ops"] = {"tier": "T2"}
    (install / "registry.json").write_text(json.dumps(reg))
    LIVE.add("ops")
    try:
        out, args = run("answer_menu", session="ops", option="1")
        assert args is not None and args["session"] == "ops", out
        out, args = run("agent_message", to_agent="Ops", subject="x")     # not the id as written: still asked
        assert out.startswith("NOT RUN: 'Ops' could mean more than one agent")
        assert mod._asked_parent("ops", "dev-new") == "ops"
    finally:
        LIVE.discard("ops")


def test_reports_to_takes_only_an_exact_name_of_a_registered_seat(mod, install):
    assert mod._asked_parent("Front Dsk", "dev-new").startswith("NOT RUN")       # a close guess is never a parent
    c = odb.get_connection(sn.db_path_for(install))
    c.execute("INSERT INTO lineages (root, tier, runtime) VALUES ('scout', 'T2', 'claude')")
    sn.set_name(c, "scout", "Scout Lead", by="dashboard")
    c.close()
    assert mod._seat_names()["scout"] == "Scout Lead"
    assert mod._asked_parent("Scout Lead", "dev-new").startswith("NOT RUN")      # known, but not a registered seat
    assert mod._asked_parent("operator", "dev-new").startswith("NOT RUN")        # a built-in mailbox


def test_focus_entity_finds_a_renamed_agent_by_its_name():
    from services.arturo import focus_tool
    rows = {"/agents": {"agents": [{"id": "gm", "tmux_session": "gm"}, {"id": "pm-demo", "tmux_session": "pm-demo"}]},
            "/pending-approvals": {"pending": []}}
    said = focus_tool.focus_entity("front desk", rows.get, names={"gm": "Front Desk"}, observe=True, log=lambda r: None)
    assert said.startswith("[observe] I'd focus") and "gm" in said, said
    plain = focus_tool.focus_entity("front desk", rows.get, observe=True, log=lambda r: None)
    assert not plain.startswith("[observe] I'd focus"), plain
