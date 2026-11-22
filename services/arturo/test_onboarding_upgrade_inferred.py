"""An install from before onboarding existed is not a first run (orchestra-builder D-7).

onboarded() used to be only "ARTURO_STATE/onboarding.json exists", and that flag arrived with the
first-run feature. So every install that predated it was treated as fresh on upgrade: Arturo re-ran
onboarding, one operator was offered a starter team for a week, and a /clear out of the onboarding
playbook reached an agent. Now the first look at a flagless install decides once: positive evidence of
an earlier install (a T0 gm, another T0 manager, a saved operator name) writes the flag with reason
"upgrade-inferred"; none marks the install fresh and onboarding runs exactly as before.
"""
import importlib.util
import json
import os
import pathlib

import pytest

from services.arturo import onboarding_progress as prog
from services.arturo import operator_store as ops

OPENER = "[Onboarding: step=onboarding_open]\n" + "(first run: the operator just opened OrchestraOS)"


@pytest.fixture(autouse=True)
def fence(tmp_path, monkeypatch):
    """Every path this file touches is under tmp_path: HOME, the data dir and ARTURO_STATE; no tmux."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    assert os.environ["HOME"].startswith(str(tmp_path))
    assert os.environ["ORCHESTRA_DIR"].startswith(str(tmp_path))
    assert "TMUX" not in os.environ and "TMUX_PANE" not in os.environ
    yield


@pytest.fixture()
def dirs(tmp_path):
    data = tmp_path / "data"
    state = data / "state" / "arturo"
    state.mkdir(parents=True)
    assert str(state).startswith(str(tmp_path))
    return data, state


def _registry(data, agents):
    (data / "registry.json").write_text(json.dumps({"agents": agents}))


def _flag(state):
    return json.loads((state / "onboarding.json").read_text())


def _no_leftovers(state):
    assert not [p.name for p in state.iterdir() if p.name.endswith(".tmp")]


# ---- each evidence source alone ------------------------------------------------------------------

def test_a_t0_gm_alone_infers_an_upgrade_and_writes_the_flag_with_its_reason(dirs):
    data, state = dirs
    _registry(data, {"gm": {"tier": "T0"}})
    out = prog.ensure_onboarded(state, data, now=1234.5)
    assert out["onboarded"] is True and out["how"] == "inferred"
    flag = _flag(state)
    assert flag["reason"] == "upgrade-inferred" and flag["inferred_at"] == 1234.5
    assert flag["evidence"] == ["registry: gm is a T0 seat (starter team present or incomplete)"]
    _no_leftovers(state)


def test_another_t0_manager_alone_infers_an_upgrade(dirs):
    data, state = dirs
    _registry(data, {"boss": {"tier": "t0"}, "dev-x": {"tier": "T2", "reports_to": "boss"}})
    assert prog.ensure_onboarded(state, data)["how"] == "inferred"
    assert _flag(state)["evidence"] == ["starter team state: other_manager (boss is T0)"]


def test_a_saved_operator_name_alone_infers_an_upgrade_and_the_flag_never_holds_the_name(dirs):
    data, state = dirs
    ops.set_fact(state, "name", "Ada Lovelace", source="brain")
    assert prog.ensure_onboarded(state, data)["onboarded"] is True
    flag = _flag(state)
    assert flag["reason"] == "upgrade-inferred" and flag["evidence"] == ["operator.json: a name is saved"]
    assert "Ada" not in (state / "onboarding.json").read_text()


# ---- a fresh install onboards as it always did ---------------------------------------------------

@pytest.mark.parametrize("setup", ["nothing", "empty_registry", "seats_without_t0", "facts_without_name"])
def test_no_evidence_is_a_fresh_install_and_writes_no_flag(dirs, setup):
    data, state = dirs
    if setup == "empty_registry":
        _registry(data, {})
    if setup == "seats_without_t0":
        _registry(data, {"pm-x": {"tier": "T1"}, "dev-x": {"tier": "T2"}})
    if setup == "facts_without_name":
        ops.set_fact(state, "timezone", "UTC", source="brain")
    out = prog.ensure_onboarded(state, data, now=99.0)
    assert out == {"onboarded": False, "how": "fresh", "evidence": [], "unreadable": []}
    assert not (state / "onboarding.json").exists()
    assert prog.read(state)["fresh_at"] == 99.0


def test_a_team_or_name_made_during_a_fresh_first_run_is_never_read_as_an_upgrade(dirs):
    data, state = dirs
    assert prog.ensure_onboarded(state, data)["how"] == "fresh"
    prog.pin_conversation(state, "web_onb")
    ops.set_fact(state, "name", "Ada", source="brain", source_thread="web_onb")
    _registry(data, {"gm": {"tier": "T0"}})                   # the onboarding's own create_starter_team
    out = prog.ensure_onboarded(state, data)
    assert out["onboarded"] is False and out["how"] == "fresh"
    assert not (state / "onboarding.json").exists()


def test_rerun_requested_by_hand_runs_onboarding_on_an_install_with_a_team(dirs):
    data, state = dirs
    _registry(data, {"gm": {"tier": "T0"}})
    (state / prog.FILENAME).write_text(json.dumps({"rerun": True}))
    assert prog.ensure_onboarded(state, data)["onboarded"] is False
    assert not (state / "onboarding.json").exists()


# ---- an existing flag is never touched -----------------------------------------------------------

def test_an_existing_flag_is_left_byte_for_byte_whatever_the_evidence(dirs):
    data, state = dirs
    _registry(data, {"gm": {"tier": "T0"}})
    ops.set_fact(state, "name", "Ada", source="brain")
    f = state / "onboarding.json"
    f.write_text('{"finished_at": 1.0}\n')
    os.utime(f, (1000, 1000))
    out = prog.ensure_onboarded(state, data)
    assert out["onboarded"] is True and out["how"] == "flag"
    assert f.read_text() == '{"finished_at": 1.0}\n' and f.stat().st_mtime == 1000
    assert not (state / prog.FILENAME).exists()                  # nothing else written either


def test_write_flag_never_overwrites_and_leaves_no_temp_file(dirs):
    _data, state = dirs
    assert prog.write_flag(state, {"finished_at": 1.0}) is True
    assert prog.write_flag(state, {"reason": "upgrade-inferred"}) is False
    assert _flag(state) == {"finished_at": 1.0}
    _no_leftovers(state)


def test_inferring_twice_is_idempotent(dirs):
    data, state = dirs
    _registry(data, {"gm": {"tier": "T0"}})
    prog.ensure_onboarded(state, data, now=1.0)
    first = (state / "onboarding.json").read_text()
    out = prog.ensure_onboarded(state, data, now=2.0)
    assert out["how"] == "flag" and (state / "onboarding.json").read_text() == first


# ---- unreadable evidence is no evidence ----------------------------------------------------------

@pytest.mark.parametrize("which", ["registry", "registry_agents_not_a_map", "operator", "progress"])
def test_unreadable_evidence_is_undecided_writes_nothing_and_onboarding_runs(dirs, which):
    data, state = dirs
    if which == "registry":
        (data / "registry.json").write_text('{"agents": {"gm": {"tier": "T0"')    # torn write
    if which == "registry_agents_not_a_map":
        (data / "registry.json").write_text('{"agents": ["gm"]}')
    if which == "operator":
        (state / "operator.json").write_text("not json")
    if which == "progress":
        (state / prog.FILENAME).write_text("not json")
        _registry(data, {"gm": {"tier": "T0"}})
    out = prog.ensure_onboarded(state, data)
    assert out["onboarded"] is False and out["how"] == "undecided" and out["unreadable"]
    assert not (state / "onboarding.json").exists()
    if which != "progress":
        assert not (state / prog.FILENAME).exists()              # not marked fresh: the next call looks again


def test_once_the_unreadable_registry_is_repaired_the_upgrade_is_inferred(dirs):
    data, state = dirs
    (data / "registry.json").write_text("{")
    assert prog.ensure_onboarded(state, data)["how"] == "undecided"
    _registry(data, {"gm": {"tier": "T0"}})
    assert prog.ensure_onboarded(state, data)["how"] == "inferred"


def test_positive_evidence_wins_over_another_unreadable_source(dirs):
    data, state = dirs
    (data / "registry.json").write_text("{")
    ops.set_fact(state, "name", "Ada", source="brain")
    out = prog.ensure_onboarded(state, data)
    assert out["how"] == "inferred" and out["unreadable"] == ["registry.json"]


# ---- an onboarding that began before fresh_at existed (a legacy pin) -----------------------------

def test_a_legacy_pin_with_a_name_learned_on_that_thread_is_still_a_first_run(dirs):
    data, state = dirs
    (state / prog.FILENAME).write_text(json.dumps({"onboarding_conversation": "web_onb"}))
    ops.set_fact(state, "name", "Ada", source="brain", source_thread="web_onb")
    assert prog.ensure_onboarded(state, data)["onboarded"] is False
    assert not (state / "onboarding.json").exists()


def test_a_legacy_pin_on_an_install_that_already_had_a_name_or_team_is_an_upgrade(dirs):
    data, state = dirs
    (state / prog.FILENAME).write_text(json.dumps({"onboarding_conversation": "web_onb"}))
    ops.set_fact(state, "name", "Ada", source="brain")                 # learned before, not on that thread
    assert prog.ensure_onboarded(state, data)["how"] == "inferred"


# ---- through the proxy ---------------------------------------------------------------------------

def _load_proxy():
    spec = importlib.util.spec_from_file_location("arturo_proxy_upgrade", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def P(dirs, tmp_path):
    data, state = dirs
    mod = _load_proxy()
    from services.arturo.thread_store import ThreadStore
    mod._THREADS = ThreadStore(tmp_path / "threads.db")
    mod.ARTURO_STATE = state
    mod.ORCHESTRA_DIR = data
    mod.build_context = lambda **k: "BASECTX"
    mod.seat_seen = lambda name: "running"
    mod.seen = []
    replies = iter([f"reply {i}" for i in range(100)])

    def brain(messages, cid):
        mod.seen.append(messages[0]["content"])
        return (next(replies), [])
    mod._brain_reply = brain
    return mod


def test_health_on_an_upgraded_install_says_onboarded_and_hands_out_no_onboarding_thread(P, fleet_stamp):
    fleet = fleet_stamp(P)                                    # sets P.ORCHESTRA_DIR to the same tmp data dir
    _registry(P.ORCHESTRA_DIR, {"gm": {"tier": "T0"}})
    with P.app.test_client() as c:
        h = c.get("/health", environ_base={"REMOTE_ADDR": "127.0.0.1"}, headers=fleet).get_json()
    assert h["onboarded"] is True and h["onboarding_conversation"] is None
    assert _flag(P.ARTURO_STATE)["reason"] == "upgrade-inferred"


def test_a_fresh_install_still_gets_the_first_run_and_stays_in_it_after_its_team_starts(P):
    code, body = P.text_turn(OPENER, "web_onb", principal="fleet")
    assert code == 200 and "page opening for the first time" in P.seen[-1]
    assert body["onboarding"] == {"done": False} and body["onboarding_conversation"] == "web_onb"
    _registry(P.ORCHESTRA_DIR, {"gm": {"tier": "T0"}})          # onboarding started the team
    ops.set_fact(P.ARTURO_STATE, "name", "Ada", source="brain", source_thread="web_onb")
    assert P.onboarded() is False and not (P.ARTURO_STATE / "onboarding.json").exists()


@pytest.mark.parametrize("agents,team_state,inferred", [
    ({}, "absent", False),
    ({"gm": {"tier": "T0"}}, "incomplete", True),
    ({"gm": {"tier": "T0"}, "pm-x": {"tier": "T1", "role": "pm", "reports_to": "gm"},
      "dev-x": {"tier": "T2", "reports_to": "pm-x"}}, "present", True),
    ({"boss": {"tier": "T0"}}, "other_manager", True),
])
def test_the_evidence_agrees_with_starter_team_state(P, agents, team_state, inferred):
    """starter_team_state present (complete or not) or other_manager is an existing install."""
    _registry(P.ORCHESTRA_DIR, agents)
    assert P.starter_team_state()["state"] == team_state
    assert P.onboarded() is inferred
