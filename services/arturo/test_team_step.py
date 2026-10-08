"""Onboarding step 'team': Arturo leads the first run (live new-user test, finding #12).

The operator's words: "Arturo hopefully can be fed instructions to onboard the user and explain what
the hierarchy is create the agents automatically and explain the GM role".

What this pins:
- the step's state is decided SERVER-side from the registry plus what is running on each pane, in
  five states, never two: absent / incomplete / present / other_manager / unknown;
- only absent and incomplete offer to create anything, and the tool runs `orchestra starter`
  itself (never a second spawn path);
- the tool reports what it SEES on each seat afterwards — a tmux session alone is not a running
  agent (finding #10: gm's pane held a nested tmux client and Arturo called it "definitely alive");
- nothing here tells the operator to run `tmux attach`.
"""
import importlib.util
import json
import pathlib

import pytest


def _load_proxy():
    spec = importlib.util.spec_from_file_location("arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def P():
    return _load_proxy()


def _registry(tmp_path, agents):
    (tmp_path / "registry.json").write_text(json.dumps({"agents": agents}))
    return str(tmp_path)


STARTER = {
    "gm": {"tier": "T0", "always_on": True},
    "pm-website": {"tier": "T1", "reports_to": "gm"},
    "dev-website": {"tier": "T2", "reports_to": "pm-website"},
}


# ---- the directive ------------------------------------------------------------------------
def _d(ctx):
    from services.arturo import onboarding as onb
    return onb.directive("team", ctx)


def test_every_state_explains_the_three_tiers_and_the_managers_job():
    for ctx in ({"state": "absent"}, {"state": "present", "seats": []}, {"state": "unknown"},
                {"state": "other_manager", "manager": "boss"}, {"state": "incomplete", "seats": []}):
        d = _d(ctx)
        assert "T0" in d and "T1" in d and "T2" in d, ctx
        assert "manager" in d.lower() and "gm" in d, ctx


def test_absent_asks_one_question_and_names_the_tool_and_the_default_project():
    d = _d({"state": "absent"})
    assert "create_starter_team" in d
    assert "first-project" in d
    assert "one question" in d.lower()
    assert "always on" in d.lower()            # the honest cost sentence


def test_present_names_the_seats_and_creates_nothing():
    d = _d({"state": "present", "seats": [
        {"name": "gm", "tier": "T0", "seen": "running"},
        {"name": "pm-website", "tier": "T1", "seen": "running"},
        {"name": "dev-website", "tier": "T2", "seen": "running"}]})
    assert "pm-website" in d and "dev-website" in d
    assert "do not call create_starter_team" in d.lower()


def test_incomplete_reports_what_is_seen_and_offers_to_finish_with_the_known_project():
    d = _d({"state": "incomplete", "project": "website", "seats": [
        {"name": "gm", "tier": "T0", "seen": "stopped"},
        {"name": "pm-website", "tier": "T1", "seen": "running"}]})
    assert "gm (T0): a session is open but no agent is running in it" in d
    assert "project='website'" in d
    assert "create_starter_team" in d


def test_another_manager_is_never_doubled():
    d = _d({"state": "other_manager", "manager": "boss"})
    assert "boss" in d
    assert "do not call create_starter_team" in d.lower()


def test_an_unreadable_registry_is_not_an_empty_one():
    d = _d({"state": "unknown"})
    assert "do not call create_starter_team" in d.lower()


def test_the_directive_never_sends_the_operator_to_tmux_attach():
    for ctx in ({"state": "absent"}, {"state": "present", "seats": []}, {"state": "unknown"},
                {"state": "incomplete", "seats": []}, {"state": "other_manager", "manager": "boss"}):
        d = _d(ctx).lower()
        assert "never tell" in d and "tmux attach" in d   # only ever as the prohibition
        assert d.count("tmux attach") == 1


def test_the_reply_must_report_seats_as_seen_never_as_assumed():
    d = _d({"state": "absent"}).lower()
    assert "what the tool says it sees" in d


# ---- server-side state ------------------------------------------------------------------
def test_state_absent_when_no_manager(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {"hello": {"tier": "T2"}}))
    assert P.starter_team_state(seen=lambda n: "running")["state"] == "absent"


def test_state_present_when_gm_pm_and_worker_are_all_running(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, STARTER))
    st = P.starter_team_state(seen=lambda n: "running")
    assert st["state"] == "present"
    assert [s["name"] for s in st["seats"]] == ["gm", "pm-website", "dev-website"]
    assert st["project"] == "website"


def test_a_session_with_no_agent_in_it_makes_the_team_incomplete(P, tmp_path, monkeypatch):
    # finding #10: gm's tmux session existed, with a tmux client in it instead of claude
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, STARTER))
    st = P.starter_team_state(seen=lambda n: "stopped" if n == "gm" else "running")
    assert st["state"] == "incomplete"
    assert st["seats"][0] == {"name": "gm", "tier": "T0", "seen": "stopped"}


def test_a_manager_with_no_project_under_it_is_incomplete(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {"gm": {"tier": "T0"}}))
    st = P.starter_team_state(seen=lambda n: "running")
    assert st["state"] == "incomplete" and st.get("project") is None


def test_a_manager_not_named_gm_is_other_manager(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {"boss": {"tier": "T0"}}))
    st = P.starter_team_state(seen=lambda n: "running")
    assert st == {"state": "other_manager", "manager": "boss"}


def test_an_unreadable_registry_is_unknown(P, tmp_path, monkeypatch):
    (tmp_path / "registry.json").write_text("{not json")
    monkeypatch.setattr(P, "ORCHESTRA_DIR", str(tmp_path))
    assert P.starter_team_state(seen=lambda n: "running")["state"] == "unknown"


def test_no_registry_file_yet_is_absent(P, tmp_path, monkeypatch):
    # a fresh install has not written one; that IS known-empty, unlike an unreadable one
    monkeypatch.setattr(P, "ORCHESTRA_DIR", str(tmp_path))
    assert P.starter_team_state(seen=lambda n: "running")["state"] == "absent"


# ---- seat liveness ------------------------------------------------------------------------
@pytest.mark.parametrize("detector_state,seen", [
    ("idle", "running"), ("working", "running"), ("waiting_permission", "running"),
    ("stopped", "stopped"), ("unknown", "unknown")])
def test_seat_seen_maps_the_detector(P, monkeypatch, detector_state, seen):
    monkeypatch.setattr(P, "_tmux_session_exists", lambda n: True)
    monkeypatch.setattr(P, "_detector_state", lambda n: detector_state)
    assert P.seat_seen("gm") == seen


def test_seat_seen_without_a_session(P, monkeypatch):
    monkeypatch.setattr(P, "_tmux_session_exists", lambda n: False)
    monkeypatch.setattr(P, "_detector_state", lambda n: (_ for _ in ()).throw(AssertionError("not consulted")))
    assert P.seat_seen("gm") == "no session"


# ---- the tool -----------------------------------------------------------------------------
def test_the_tool_runs_orchestra_starter_and_reports_each_seat_as_seen(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    ran = {}

    def fake_run(plan, timeout=120):
        ran["argv"], ran["env"] = plan.argv, plan.env
        return True, "starter team up"
    monkeypatch.setattr(P, "_run_commission", fake_run)
    monkeypatch.setattr(P, "seat_seen", lambda n: "stopped" if n == "dev-website" else "running")
    spawned = []
    monkeypatch.setattr(P, "_record_spawned_this_turn", spawned.append)
    out = P.execute_tool("create_starter_team", {"project": "website"})
    assert ran["argv"][1:] == ["starter", "--project", "website"]
    assert ran["argv"][0].endswith("bin/orchestra")
    assert "gm (T0): running" in out
    assert "pm-website (T1): running" in out
    assert "dev-website (T2): a session is open but no agent is running in it" in out
    assert spawned == ["gm", "pm-website"]          # only what was SEEN running
    assert "tmux attach" not in out


def test_the_tool_defaults_the_project(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    ran = {}
    monkeypatch.setattr(P, "_run_commission", lambda plan, timeout=120: (ran.setdefault("argv", plan.argv), (True, ""))[1])
    monkeypatch.setattr(P, "seat_seen", lambda n: "running")
    P.execute_tool("create_starter_team", {})
    assert ran["argv"][-2:] == ["--project", "first-project"]


@pytest.mark.parametrize("bad", ["My Website", "web.site", "-x", "a" * 41, "../etc"])
def test_the_tool_refuses_a_project_name_before_running_anything(P, tmp_path, monkeypatch, bad):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    monkeypatch.setattr(P, "_run_commission", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    out = P.execute_tool("create_starter_team", {"project": bad})
    assert "lowercase" in out


def test_the_tool_refuses_when_another_manager_exists(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {"boss": {"tier": "T0"}}))
    monkeypatch.setattr(P, "_run_commission", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    out = P.execute_tool("create_starter_team", {"project": "website"})
    assert "boss" in out and "did not" in out


def test_the_tool_refuses_when_the_registry_is_unreadable(P, tmp_path, monkeypatch):
    (tmp_path / "registry.json").write_text("{not json")
    monkeypatch.setattr(P, "ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setattr(P, "_run_commission", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    out = P.execute_tool("create_starter_team", {"project": "website"})
    assert "could not" in out.lower()


def test_a_failed_starter_still_reports_what_is_seen(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    monkeypatch.setattr(P, "_run_commission", lambda plan, timeout=120: (False, "starter stopped at pm-website: boom"))
    monkeypatch.setattr(P, "seat_seen", lambda n: "running" if n == "gm" else "no session")
    out = P.execute_tool("create_starter_team", {"project": "website"})
    assert out.startswith("FAILED")
    assert "boom" in out
    assert "gm (T0): running" in out and "pm-website (T1): not started" in out


def test_the_tool_is_offered_to_the_brain_and_is_side_effecting(P):
    from services.arturo import voice_guards as vg
    names = [t["function"]["name"] for t in P.TOOLS]
    assert "create_starter_team" in names
    assert vg.is_side_effecting("create_starter_team")


# ---- /text carries the state, so the home page can finish the step by effect ---------------
def test_a_team_turn_returns_the_team_state_after_the_turn(P, monkeypatch, tmp_path):
    monkeypatch.setattr(P, "ARTURO_STATE", tmp_path)
    states = iter([{"state": "absent"}, {"state": "present", "seats": []}])
    monkeypatch.setattr(P, "starter_team_state", lambda seen=None: next(states))
    monkeypatch.setattr(P, "_brain_reply", lambda messages, cid: ("Your team is up.", ["create_starter_team"], ["gm"]))
    monkeypatch.setattr(P, "_conversation_history", lambda cid: [])
    monkeypatch.setattr(P, "_record_text_turn", lambda **kw: None)
    status, body = P.text_turn("[Onboarding: step=team]\nyes, call it website", "web_t1")
    assert status == 200
    assert body["team"] == {"state": "present", "seats": []}


def test_a_turn_outside_the_team_step_carries_no_team_field(P, monkeypatch, tmp_path):
    monkeypatch.setattr(P, "ARTURO_STATE", tmp_path)
    monkeypatch.setattr(P, "starter_team_state", lambda seen=None: (_ for _ in ()).throw(AssertionError("not consulted")))
    monkeypatch.setattr(P, "_brain_reply", lambda messages, cid: ("hi", [], []))
    monkeypatch.setattr(P, "_conversation_history", lambda cid: [])
    monkeypatch.setattr(P, "_record_text_turn", lambda **kw: None)
    status, body = P.text_turn("hello", "web_t2")
    assert status == 200 and "team" not in body


def test_the_stream_fallback_passes_the_team_state_through():
    from services.arturo import text_stream as ts
    body = {"ok": True, "reply_text": "ok", "tools_called": [], "spawned": [], "brain": {"kind": "runtime"},
            "team": {"state": "present"}}
    events = list(ts._whole_reply("t1", "c1", lambda: (200, body), brain=None))
    end = [e for e in events if e["event"] == "turn.end"][-1]
    assert end["data"]["team"] == {"state": "present"}
