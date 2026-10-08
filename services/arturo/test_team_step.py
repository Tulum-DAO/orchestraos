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


def test_state_starting_while_a_run_is_in_progress(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    monkeypatch.setattr(P, "starter_running", lambda: True)
    assert P.starter_team_state(seen=lambda n: "running")["state"] == "starting"


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


# ---- the tool ----------------------------------------------------------------------------------
import contextlib
import threading


@contextlib.contextmanager
def _turn(P, offered=True, opener=False, cid="web_c1"):
    tok = P._TEAM_TURN.set({"conversation_id": cid, "opener": opener, "offered": offered, "declined": False})
    try:
        yield
    finally:
        P._TEAM_TURN.reset(tok)


def _no_run(*a, **k):
    raise AssertionError("orchestra starter ran")


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
    with _turn(P):
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
    with _turn(P):
        P.execute_tool("create_starter_team", {})
    assert ran["argv"][-2:] == ["--project", "first-project"]


def test_a_fresh_install_with_no_registry_file_proceeds_like_the_directive_offered(P, tmp_path, monkeypatch):
    # review #268 should-fix 4: the directive said "absent" (offer) while the tool said "could not
    # check" (refuse). Both now read starter_team_state.
    monkeypatch.setattr(P, "ORCHESTRA_DIR", str(tmp_path))
    ran = []
    monkeypatch.setattr(P, "_run_commission", lambda plan, timeout=120: (ran.append(plan.argv), (True, ""))[1])
    monkeypatch.setattr(P, "seat_seen", lambda n: "running")
    with _turn(P):
        P.execute_tool("create_starter_team", {"project": "website"})
    assert ran


@pytest.mark.parametrize("bad", ["My Website", "web.site", "-x", "a" * 41, "../etc"])
def test_the_tool_refuses_a_project_name_before_running_anything(P, tmp_path, monkeypatch, bad):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    monkeypatch.setattr(P, "_run_commission", _no_run)
    with _turn(P):
        out = P.execute_tool("create_starter_team", {"project": bad})
    assert "lowercase" in out


def test_the_tool_refuses_when_another_manager_exists(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {"boss": {"tier": "T0"}}))
    monkeypatch.setattr(P, "_run_commission", _no_run)
    with _turn(P):
        out = P.execute_tool("create_starter_team", {"project": "website"})
    assert "boss" in out and "did not" in out


def test_gm_beside_another_manager_is_not_refused_for_the_other_one(P, tmp_path, monkeypatch):
    # the old tool read existing_manager(), which could return the other T0 and refuse
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {"aaa": {"tier": "T0"}, "gm": {"tier": "T0"}}))
    ran = []
    monkeypatch.setattr(P, "_run_commission", lambda plan, timeout=120: (ran.append(1), (True, ""))[1])
    monkeypatch.setattr(P, "seat_seen", lambda n: "running" if n == "gm" else "no session")
    with _turn(P):
        P.execute_tool("create_starter_team", {"project": "website"})
    assert ran


def test_the_tool_refuses_when_the_registry_is_unreadable(P, tmp_path, monkeypatch):
    (tmp_path / "registry.json").write_text("{not json")
    monkeypatch.setattr(P, "ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setattr(P, "_run_commission", _no_run)
    with _turn(P):
        out = P.execute_tool("create_starter_team", {"project": "website"})
    assert "could not" in out.lower()


def test_a_running_team_is_not_started_again(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, STARTER))
    monkeypatch.setattr(P, "_run_commission", _no_run)
    monkeypatch.setattr(P, "seat_seen", lambda n: "running")
    with _turn(P):
        out = P.execute_tool("create_starter_team", {"project": "website"})
    assert "already running" in out and "dev-website (T2): running" in out


def test_a_failed_starter_still_reports_what_is_seen(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    monkeypatch.setattr(P, "_run_commission", lambda plan, timeout=120: (False, "starter stopped at pm-website: boom"))
    monkeypatch.setattr(P, "seat_seen", lambda n: "running" if n == "gm" else "no session")
    with _turn(P):
        out = P.execute_tool("create_starter_team", {"project": "website"})
    assert out.startswith("FAILED")
    assert "boom" in out
    assert "gm (T0): running" in out and "pm-website (T1): not started" in out


def test_the_tools_are_offered_to_the_brain_and_are_side_effecting(P):
    from services.arturo import voice_guards as vg
    names = [t["function"]["name"] for t in P.TOOLS]
    for tool in ("create_starter_team", "decline_starter_team"):
        assert tool in names
        assert vg.is_side_effecting(tool)


# ---- the operator's yes, enforced in code (review #268 blocker) --------------------------------
def test_the_page_opener_can_never_start_the_team(P, tmp_path, monkeypatch):
    # "Introduce my team." is the PAGE's words; even with an offer on the book it is not a yes
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    monkeypatch.setattr(P, "_run_commission", _no_run)
    with _turn(P, offered=True, opener=True):
        out = P.execute_tool("create_starter_team", {"project": "website"})
    assert out.startswith("NOT STARTED")


def test_an_unrelated_turn_cannot_start_it_and_asks_instead(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    monkeypatch.setattr(P, "_run_commission", _no_run)
    P._TEAM_OFFERS.clear()
    with _turn(P, offered=False, cid="web_unrelated"):
        out = P.execute_tool("create_starter_team", {"project": "website"})
    assert out.startswith("NOT STARTED") and "always on" in out
    assert "web_unrelated" in P._TEAM_OFFERS            # so the operator's NEXT turn can say yes


def test_voice_and_other_turns_without_a_conversation_never_start_it(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    monkeypatch.setattr(P, "_run_commission", _no_run)
    out = P.execute_tool("create_starter_team", {"project": "website"})
    assert out.startswith("NOT STARTED")


def test_an_offer_is_good_for_exactly_the_next_turn(P):
    P._TEAM_OFFERS.clear()
    P._offer_team("web_o1")
    assert P._begin_team_turn("web_o1", "team")["offered"] is True
    assert P._begin_team_turn("web_o1", "team")["offered"] is False     # taken: a later turn has none
    assert P._begin_team_turn("web_other", "team")["offered"] is False  # never another conversation's


def test_an_expired_offer_is_not_a_yes(P, monkeypatch):
    P._TEAM_OFFERS.clear()
    P._offer_team("web_o2")
    monkeypatch.setattr(P.time, "time", lambda: 10 ** 12)
    assert P._begin_team_turn("web_o2", "team")["offered"] is False


def test_the_opener_turn_is_marked_as_the_opener(P):
    assert P._begin_team_turn("web_o3", "onboarding_open")["opener"] is True
    assert P._begin_team_turn("web_o3", "onboarding")["opener"] is False


def test_an_offer_is_spent_by_the_run_it_allowed(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    runs = []
    monkeypatch.setattr(P, "_run_commission", lambda plan, timeout=120: (runs.append(1), (True, ""))[1])
    monkeypatch.setattr(P, "seat_seen", lambda n: "no session")
    P._TEAM_OFFERS.clear()
    with _turn(P, offered=True, cid="web_spent"):
        P.execute_tool("create_starter_team", {"project": "website"})
        out = P.create_starter_team("website")     # the model calls it again in the same turn
    assert runs == [1] and out.startswith("NOT STARTED")


# ---- one run at a time (review #268 should-fix 3) ----------------------------------------------
def test_a_second_call_while_a_run_is_in_progress_does_not_start_another(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    monkeypatch.setattr(P, "_STARTER_WAIT_S", 0.2)
    release, runs = threading.Event(), []

    def slow_run(plan, timeout=120):
        runs.append(1)
        release.wait(5)
        return True, ""
    monkeypatch.setattr(P, "_run_commission", slow_run)
    monkeypatch.setattr(P, "seat_seen", lambda n: "no session")
    try:
        with _turn(P, cid="web_tab1"):
            first = P.create_starter_team("website")
        assert "Still setting up" in first             # the turn answers; the run goes on
        assert P.starter_running()
        assert P.starter_team_state()["state"] == "starting"
        with _turn(P, cid="web_tab2"):
            second = P.create_starter_team("website")
        assert "Already setting up" in second
        assert runs == [1]
    finally:
        release.set()
    for _ in range(50):
        if not P.starter_running():
            break
        threading.Event().wait(0.05)
    assert not P.starter_running()


def test_the_run_holds_a_file_lock_in_the_data_dir_so_another_process_waits(P, tmp_path, monkeypatch):
    import fcntl
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    (tmp_path / "state").mkdir()
    with open(tmp_path / "state" / "starter.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)                  # another proxy (or process) is mid-run
        try:
            assert P.starter_running()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    assert not P.starter_running()


def test_the_playbook_does_not_offer_while_a_run_is_in_progress():
    from services.arturo import onboarding as onb
    d = onb.directive("onboarding", {"team": {"state": "starting", "seats": [{"name": "gm", "tier": "T0", "seen": "running"}]}})
    assert "Do not offer it again" in d
    assert "gm (T0): running" in d


# ---- a decline is explicit (review #268 should-fix 2) ------------------------------------------
def test_decline_marks_the_turn(P):
    with _turn(P, cid="web_d1"):
        P.execute_tool("decline_starter_team", {})
        assert P._TEAM_TURN.get()["declined"] is True


def test_the_playbook_names_the_decline_tool():
    from services.arturo import onboarding as onb
    assert "decline_starter_team" in onb.directive("onboarding", {"team": {"state": "absent"}})


# ---- /text carries what the turn did, so the page acts by effect -------------------------------
def _stub_turn(P, monkeypatch, tmp_path, reply=("ok", [], [])):
    monkeypatch.setattr(P, "ARTURO_STATE", tmp_path)
    monkeypatch.setattr(P, "_brain_reply", lambda messages, cid: reply)
    monkeypatch.setattr(P, "_conversation_history", lambda cid: [])
    monkeypatch.setattr(P, "_record_text_turn", lambda **kw: None)


def test_an_onboarding_turn_says_whether_onboarding_is_done(P, monkeypatch, tmp_path):
    _stub_turn(P, monkeypatch, tmp_path)
    monkeypatch.setattr(P, "starter_team_state", lambda seen=None: {"state": "absent"})
    _, body = P.text_turn("[Onboarding: step=onboarding]\nhi", "web_t1")
    assert body["onboarding"] == {"done": False}


def test_a_turn_outside_onboarding_carries_no_onboarding_field(P, monkeypatch, tmp_path):
    _stub_turn(P, monkeypatch, tmp_path, ("hi", [], []))
    monkeypatch.setattr(P, "starter_team_state", lambda seen=None: (_ for _ in ()).throw(AssertionError("not consulted")))
    status, body = P.text_turn("hello", "web_t2")
    assert status == 200 and "onboarding" not in body


def test_the_team_card_puts_an_offer_on_the_book_for_the_operators_reply(P, monkeypatch, tmp_path):
    _stub_turn(P, monkeypatch, tmp_path)
    monkeypatch.setattr(P, "starter_team_state", lambda seen=None: {"state": "absent"})

    def brain(messages, cid):
        P.execute_tool("ask_choices", {"options": ["Set it up", "Not now"], "purpose": "starter_team"})
        return "Shall I set up your team?", ["ask_choices"], []
    monkeypatch.setattr(P, "_brain_reply", brain)
    P._TEAM_OFFERS.clear()
    _, body = P.text_turn("[Onboarding: step=onboarding_open]\n(first run: the operator just opened OrchestraOS)", "web_t3")
    assert "web_t3" in P._TEAM_OFFERS
    assert body["choices"]["options"] == ["Set it up", "Not now"]
    assert "always on" in body["choices"]["note"]           # the server's cost line, not the model's


def test_the_operators_reply_to_the_card_may_start_the_team_end_to_end(P, monkeypatch, tmp_path):
    # opener (card shown, create refused) -> operator's reply turn: the tool runs inside that turn
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    monkeypatch.setattr(P, "ARTURO_STATE", tmp_path)
    monkeypatch.setattr(P, "_conversation_history", lambda cid: [])
    monkeypatch.setattr(P, "_record_text_turn", lambda **kw: None)
    monkeypatch.setattr(P, "seat_seen", lambda n: "running")
    runs, results = [], []
    monkeypatch.setattr(P, "_run_commission", lambda plan, timeout=120: (runs.append(1), (True, ""))[1])

    def brain(messages, cid):                       # a brain that ALWAYS reaches for both
        results.append(P.execute_tool("create_starter_team", {"project": "website"}))
        P.execute_tool("ask_choices", {"options": ["Set it up", "Not now"], "purpose": "starter_team"})
        return "done", ["create_starter_team", "ask_choices"], []
    monkeypatch.setattr(P, "_brain_reply", brain)
    P._TEAM_OFFERS.clear()
    P.text_turn("[Onboarding: step=onboarding_open]\n(first run: hello)", "web_t4")
    assert runs == [] and results[0].startswith("NOT STARTED")       # the opener could not
    P.text_turn("[Onboarding: step=onboarding]\nSet it up", "web_t4")
    assert runs == [1]                                                # the reply to the card could


def test_a_turn_without_a_card_before_it_cannot_start_the_team(P, monkeypatch, tmp_path):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    _stub_turn(P, monkeypatch, tmp_path)
    monkeypatch.setattr(P, "_run_commission", _no_run)
    out = []
    monkeypatch.setattr(P, "_brain_reply", lambda m, c: (out.append(P.execute_tool("create_starter_team", {})), ("ok", [], []))[1])
    P._TEAM_OFFERS.clear()
    P.text_turn("[Onboarding: step=onboarding]\nset up my team", "web_t5")
    assert out[0].startswith("NOT STARTED")


def test_the_stream_fallback_passes_the_cards_through():
    from services.arturo import text_stream as ts
    body = {"ok": True, "reply_text": "ok", "tools_called": [], "spawned": [], "brain": {"kind": "runtime"},
            "choices": {"options": ["a", "b"], "multi": False, "purpose": "other"},
            "pair_card": {"device": "iPhone"}, "onboarding": {"done": False}}
    events = list(ts._whole_reply("t1", "c1", lambda: (200, body), brain=None))
    end = [e for e in events if e["event"] == "turn.end"][-1]["data"]
    assert end["choices"]["options"] == ["a", "b"] and end["pair_card"] == {"device": "iPhone"}
    assert end["onboarding"] == {"done": False}


# ---- delta review of 24140d3 (orchestraos-builder) -----------------------------------------------
def test_a_decline_on_the_opener_is_ignored(P):
    # the page's "Introduce my team." is not the operator's no either
    with _turn(P, opener=True, cid="web_r1"):
        out = P.execute_tool("decline_starter_team", {})
        assert P._TEAM_TURN.get()["declined"] is False
    assert "not" in out.lower()


def test_a_call_while_a_run_is_in_progress_says_so_even_without_an_offer(P, tmp_path, monkeypatch):
    # after a "Still setting up" turn there is no new offer; the next call must not say "NOT STARTED"
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    monkeypatch.setattr(P, "starter_running", lambda: True)
    monkeypatch.setattr(P, "_run_commission", _no_run)
    monkeypatch.setattr(P, "seat_seen", lambda n: "no session")
    with _turn(P, offered=False, cid="web_r2"):
        out = P.create_starter_team("website")
    assert out.startswith("Already setting up")


def test_a_thread_that_cannot_start_releases_the_lock(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", _registry(tmp_path, {}))
    monkeypatch.setattr(P, "seat_seen", lambda n: "no session")

    class _NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")
    monkeypatch.setattr(P._threading, "Thread", _NoThread)
    with _turn(P, cid="web_r3"):
        out = P.create_starter_team("website")
    assert out.startswith("FAILED")
    assert not P._STARTER_LOCK.locked()
    assert P.starter_team_state(seen=lambda n: "no session")["state"] == "absent"


def test_the_starter_child_is_told_its_parent_holds_the_lock(P):
    """_run_starter holds <data>/state/starter.lock and runs `orchestra starter` as its child; the
    CLI now takes that same lock (orchestra_cli seats._starter_lock), so without this flag Arturo's
    own run would refuse itself. An operator's own CLI run still waits its turn."""
    plan = P.starter_plan("website")
    assert plan.env.get("ORCHESTRA_STARTER_LOCK_HELD") == "1"
