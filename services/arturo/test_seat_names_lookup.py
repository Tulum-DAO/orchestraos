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
