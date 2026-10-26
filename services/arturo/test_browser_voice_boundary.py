"""A dashboard (browser) voice call: full tools minus run_command / async_task (the voice safety boundary;
kill_agent is back on, the operator 2026-10-24, and never stops Arturo itself). ask_gm and deep_query still reach the deep brain through their own nested async_task."""
import pytest

from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture
from services.arturo.test_non_fleet_allowlist import tripwires  # noqa: F401

EXCLUDED = ("run_command", "async_task")


def _turn(P, surface):
    return P._voice_turn("00000000-0000-4000-8000-0000000000aa", "fleet", surface)


def _names(tools):
    return {t["function"]["name"] for t in tools}


def test_a_browser_voice_call_is_offered_everything_but_the_two(P):
    offered = _names(P._bound_tools(_turn(P, "web"), use_current=False))
    assert not offered & set(EXCLUDED)
    assert offered == _names(P.TOOLS) - set(EXCLUDED)


@pytest.mark.parametrize("surface", ["watch", "phone", None])
def test_other_fleet_voice_surfaces_are_unchanged(P, surface):
    assert _names(P._bound_tools(_turn(P, surface), use_current=False)) == _names(P.TOOLS)


@pytest.mark.parametrize("name", EXCLUDED)
def test_the_model_calling_one_directly_is_refused_and_runs_nothing(P, tripwires, name):
    tok = P._TEAM_TURN.set(_turn(P, "web"))
    try:
        out = P.execute_tool(name, {"command": "id", "session_name": "gm", "tool_name": "gm_command",
                                    "tool_args": {"prompt": "x"}})
    finally:
        P._TEAM_TURN.reset(tok)
    assert out.startswith("NOT RUN") and name in out and "dashboard voice call" in out


def test_ask_gm_still_reaches_the_deep_brain(P, monkeypatch):
    seen = []
    real = P.execute_tool

    def spy(name, args, user_turns=None):
        if name == "async_task":
            seen.append(P._non_fleet_refusal(name, P._TEAM_TURN.get()))
            return "queued"
        return real(name, args, user_turns)
    monkeypatch.setattr(P, "execute_tool", spy)
    tok = P._TEAM_TURN.set(_turn(P, "web"))
    try:
        real("ask_gm", {"request": "what is the fleet doing"})
    finally:
        P._TEAM_TURN.reset(tok)
    assert seen == [None]                      # the nested async_task was not refused


def test_the_browser_voice_prompt_points_deep_questions_at_ask_gm(P):
    out = P._browser_voice_prompt("Any deep question you can't answer instantly, you route to your own deep brain "
                                  "via async_task (which guarantees a Telegram answer back to the operator).")
    assert "via ask_gm" in out and "via async_task" not in out
    assert "run_command and async_task are not available" in out


def test_stopping_agents_by_voice_is_back(P):
    assert "kill_agent" in _names(P._bound_tools(_turn(P, "web"), use_current=False))
    assert P._non_fleet_refusal("kill_agent", _turn(P, "web")) is None


def test_kill_agent_never_stops_arturo_and_matches_exactly(P, monkeypatch):
    me = P._process_ancestry()
    assert P._hosts_arturo("ops", panes={"ops": {max(me)}})          # a pane that is one of our parents
    assert not P._hosts_arturo("pm-first-project", panes={"pm-first-project": {999999}})
    assert P._hosts_arturo("arturo-voice", panes={})
    ran = []
    monkeypatch.setattr(P, "_hosts_arturo", lambda s: s == "home")
    monkeypatch.setattr(P, "_live_seats", lambda: ["home", "pm", "pm-first-project"])
    monkeypatch.setattr(P, "_run_on_machine", lambda mac, vps: ran.append(vps) or (True, "", "vps"))
    assert P._execute_tool_impl("kill_agent", {"session_name": "home"}).startswith("NOT RUN") and not ran
    tok = P._TEAM_TURN.set(_turn(P, "web"))
    try:
        P._execute_tool_impl("kill_agent", {"session_name": "pm"})
    finally:
        P._TEAM_TURN.reset(tok)
    assert ran == ["tmux kill-session -t =pm"]                          # exact: never "pm-first-project"


SEATS = ["gm", "pm-first-project", "dev-first-project", "dev7", "ops_a", "ops-a"]


@pytest.mark.parametrize("said", ["pm first project", "PM first project", "p.m. first project", "pm_first_project",
                                  "pm dash first dash project", "Pm-First-Project"])
def test_a_spoken_agent_name_is_the_live_seat(P, said):
    args, refusal = P._resolve_agent_names("kill_agent", {"session_name": said}, seats=SEATS)
    assert refusal is None and args["session_name"] == "pm-first-project"


def test_names_resolve_in_every_tool_that_names_an_agent(P):
    assert P._resolve_agent_names("get_agent_output", {"session_name": "dev seven"}, seats=SEATS)[0]["session_name"] == "dev7"
    a, _ = P._resolve_agent_names("agent_message", {"from_agent": "G M", "to_agent": "dev first project",
                                                    "subject": "s", "body": "b"}, seats=SEATS)
    assert (a["from_agent"], a["to_agent"], a["body"]) == ("gm", "dev-first-project", "b")
    assert P._resolve_agent_names("answer_menu", {"session": "gm", "option": "1"}, seats=SEATS)[0]["session"] == "gm"


def test_two_seats_that_sound_alike_are_a_question_not_a_guess(P):
    _, refusal = P._resolve_agent_names("kill_agent", {"session_name": "ops a"}, seats=SEATS)
    assert refusal.startswith("NOT RUN") and "ops-a" in refusal and "ops_a" in refusal and "Ask the operator" in refusal
    assert P._resolve_agent_names("kill_agent", {"session_name": "ops-a"}, seats=SEATS)[1] is None   # exact wins


def test_a_new_seat_and_an_unknown_one_get_seat_form(P):
    assert P._resolve_agent_names("spawn_agent", {"session_name": "research helper two", "task": "x"},
                                  seats=SEATS)[0]["session_name"] == "research-helper-2"
    assert P._resolve_agent_names("inject_message", {"session_name": "mac builder", "message": "hi"},
                                  seats=SEATS)[0]["session_name"] == "mac-builder"
    args = {"session_name": "pm first project"}
    P._resolve_agent_names("kill_agent", args, seats=SEATS)
    assert args == {"session_name": "pm first project"}                    # the caller's dict is untouched


def test_a_shortened_name_is_used_to_look_but_asked_about_before_stopping(P):
    assert P._resolve_agent_names("get_agent_output", {"session_name": "pm first"}, seats=SEATS)[0]["session_name"] == "pm-first-project"
    _, refusal = P._resolve_agent_names("kill_agent", {"session_name": "pm first"}, seats=SEATS)
    assert "Did the operator mean pm-first-project?" in refusal
    _, refusal = P._resolve_agent_names("get_agent_output", {"session_name": "first"}, seats=["first-a", "first-b"])
    assert "not sure which agent 'first' means: first-a, first-b" in refusal


@pytest.mark.parametrize("hostile", ["x; rm -rf ~", "gm$(id)", "pm 'first' project", "a\nb"])
def test_a_hostile_name_is_never_rewritten_into_a_real_one(P, hostile):
    args, refusal = P._resolve_agent_names("kill_agent", {"session_name": hostile}, seats=SEATS)
    assert args["session_name"] == hostile and refusal is None      # left as sent: the seat-name check refuses it



def test_any_naming_style_and_word_order_is_understood(P):
    camel = ["PmFirstProject", "devSeven", "gm"]
    r = lambda said, seats, tool="get_agent_output": P._resolve_agent_names(tool, {"session_name": said}, seats=seats)
    assert r("pm first project", camel)[0]["session_name"] == "PmFirstProject"
    assert r("dev 7", camel)[0]["session_name"] == "devSeven"
    assert r("first project pm", ["pm_first_project"])[0]["session_name"] == "pm_first_project"
    assert r("pmfirstproject", ["pm-first-project"])[0]["session_name"] == "pm-first-project"
    assert r("p m first project", ["pm-first-project"])[0]["session_name"] == "pm-first-project"


def test_a_speech_to_text_slip_is_used_to_look_but_asked_about_before_stopping(P):
    seats = ["pm-first-project", "dev-first-project", "gm"]
    args, refusal = P._resolve_agent_names("get_agent_output", {"session_name": "pm furst project"}, seats=seats)
    assert refusal is None and args["session_name"] == "pm-first-project"
    _, refusal = P._resolve_agent_names("kill_agent", {"session_name": "pm furst project"}, seats=seats)
    assert "Did the operator mean pm-first-project?" in refusal
    _, refusal = P._resolve_agent_names("get_agent_output", {"session_name": "first project"}, seats=seats)
    assert "not sure which agent" in refusal                 # two seats start that way: ask
    args, refusal = P._resolve_agent_names("kill_agent", {"session_name": "pm first project"}, seats=seats)
    assert refusal is None and args["session_name"] == "pm-first-project"   # same words: confident enough to stop


def test_a_new_seat_follows_this_installs_naming_style(P):
    spawn = lambda said, seats: P._resolve_agent_names("spawn_agent", {"session_name": said, "task": "x"}, seats=seats)[0]["session_name"]
    assert spawn("research helper", ["pm_first_project", "dev_first_project"]) == "research_helper"
    assert spawn("research helper", ["PmFirstProject", "devSeven"]) == "researchHelper"
    assert spawn("research helper", []) == "research-helper"


@pytest.mark.parametrize("said", ["the acme project manager", "Acme's project manager", "the project manager for acme",
                                  "acme PM", "pm acme"])
def test_a_role_and_a_client_name_the_seat(P, said):
    seats = ["gm", "pm-acme", "dev-acme", "pm-northwind-traders"]
    args, refusal = P._resolve_agent_names("inject_message", {"session_name": said, "message": "hi"}, seats=seats)
    assert refusal is None and args["session_name"] == "pm-acme"


def test_roles_partial_names_and_the_general_manager(P):
    seats = ["gm", "pm-acme", "dev-acme", "pm-northwind-traders"]
    look = lambda said: P._resolve_agent_names("get_agent_output", {"session_name": said}, seats=seats)
    assert look("the general manager")[0]["session_name"] == "gm"
    assert look("the acme developer")[0]["session_name"] == "dev-acme"
    assert look("the northwind project manager")[0]["session_name"] == "pm-northwind-traders"
    assert "not sure which agent 'acme' means: dev-acme, pm-acme" in look("acme")[1]
    _, refusal = P._resolve_agent_names("kill_agent", {"session_name": "the northwind project manager"}, seats=seats)
    assert "Did the operator mean pm-northwind-traders?" in refusal           # partial: ask before stopping
    spawn = P._resolve_agent_names("spawn_agent", {"session_name": "the globex project manager", "task": "x"}, seats=seats)
    assert spawn[0]["session_name"] == "pm-globex"


def test_a_bare_role_names_the_only_seat_with_it_or_asks(P):
    look = lambda said, seats: P._resolve_agent_names("get_agent_output", {"session_name": said}, seats=seats)
    assert look("the project manager", ["gm", "pm-first-project", "dev-first-project"])[0]["session_name"] == "pm-first-project"
    assert "not sure which agent" in look("the project manager", ["gm", "pm-acme", "pm-globex"])[1]
    _, refusal = P._resolve_agent_names("kill_agent", {"session_name": "the project manager"},
                                        seats=["gm", "pm-first-project"])
    assert "Did the operator mean pm-first-project?" in refusal


def test_a_new_seat_reports_to_its_place_in_the_org_chart_never_to_arturo(P):
    reg = {"agents": {"gm": {"tier": "T0"}, "pm-first-project": {"tier": "T1"}, "dev-first-project": {"tier": "T2"}}}
    assert P.seat_parent("dev-first-project-2", reg) == "gm"            # no pm named exactly like it: the manager
    assert P.seat_parent("qa-first-project", reg) == "pm-first-project"
    assert P.seat_parent("dev-first-project", {"agents": {**reg["agents"], "dev-first-project": None}}) == "pm-first-project"
    assert P.seat_parent("scratch-helper", reg) == "gm"
    assert P.seat_parent("scratch-helper", {"agents": {}}) is None       # no manager: starts as a lead
    plan = P.commission_plan("scratch-helper", "say ready", parent="gm")
    assert plan.env["PARENT_AGENT_ID"] == "gm"
    assert "PARENT_AGENT_ID" not in P.commission_plan("scratch-helper", "say ready").env


def test_a_seat_named_for_its_project_lands_under_that_projects_manager(P):
    """2026-10-24: 'qa-scratch-first-project' went under the gm as a lead, not under pm-first-project."""
    reg = {"agents": {"gm": {"tier": "T0"}, "pm-first-project": {"tier": "T1"}}}
    assert P.seat_parent("qa-scratch-first-project", reg) == "pm-first-project"
    assert P.seat_parent("qa-scratch", reg) == "gm"
    assert P._asked_parent("pm first project", "qa-x", reg) == "pm-first-project"
    assert P._asked_parent("", "qa-x", reg) is None
    assert P._asked_parent("arturo", "qa-x", {"agents": {**reg["agents"], "arturo": {}}}).startswith("NOT RUN")
    assert P._asked_parent("pm-nowhere", "qa-x", reg).startswith("NOT RUN")


def test_a_second_spawn_moments_after_the_first_asks_whether_it_was_a_correction(P, monkeypatch):
    """2026-10-24: 'a QA scratch agent' ... 'like a T-two agent' made two seats. The second waits for a yes."""
    monkeypatch.setattr(P, "_live_seats", lambda: ["gm", "qa-scratch"])
    monkeypatch.setattr(P, "_RECENT_SPAWNS", {})
    c = P._CALLER_CONV.set("web_spawns")
    try:
        assert P._spawned_moments_ago("qa-scratch", now=1000.0) is None
        again = P._spawned_moments_ago("qa-scratch-first-project", now=1030.0)
        assert again.startswith("NOT RUN") and "'qa-scratch' 30s ago" in again and "another=true" in again
        assert P._spawned_moments_ago("qa-scratch-first-project", another=True, now=1031.0) is None
        assert P._spawned_moments_ago("dev-later", now=1031.0 + P._SPAWN_AGAIN_S) is None   # minutes later: fine
        monkeypatch.setattr(P, "_live_seats", lambda: ["gm"])                               # the first one failed
        P._RECENT_SPAWNS["web_spawns"] = ("qa-broken", 2000.0)
        assert P._spawned_moments_ago("qa-fixed", now=2010.0) is None
    finally:
        P._CALLER_CONV.reset(c)


def test_list_agents_reads_the_org_chart_from_the_registry_when_the_hierarchy_is_empty(P):
    """2026-10-24: list_agents said 'No registered agents found' for five registered seats, so a call told the
    operator its two new seats were not real agents."""
    reg = {"agents": {"gm": {"tier": "T0"}, "pm-first-project": {"tier": "T1", "role": "pm", "reports_to": "gm"},
                      "qa-first-project": {"tier": "T2", "role": "worker", "reports_to": "pm-first-project"}}}
    rows = P._registry_hierarchy(reg)
    assert [r["agent_id"] for r in rows] == ["gm", "pm-first-project", "qa-first-project"]
    out = P._list_agents_text(rows, {"gm", "qa-first-project"}, set(), detect=lambda n: "idle")
    assert "3 registered" in out and "qa-first-project: T2 worker, reports to pm-first-project" in out
    assert "Unregistered" not in out


def test_the_call_prompt_says_agents_can_be_started_and_stopped_from_the_call(P):
    """2026-10-24: the call said it couldn't stop agents from a voice call and sent the operator to the chat."""
    assert "stop (kill_agent)" in P._BROWSER_VOICE_PROMPT_NOTE and "never send the operator to the chat" in P._BROWSER_VOICE_PROMPT_NOTE
    assert "kill_agent" not in P._BROWSER_VOICE_EXCLUDED


def test_a_worker_arturo_spawns_under_a_pm_works_in_the_projects_folder(P, monkeypatch, tmp_path):
    """A user report: a project's seats work in its folder, not the orchestraos checkout."""
    import json as _json
    monkeypatch.setattr(P, "ORCHESTRA_DIR", tmp_path)
    (tmp_path / "registry.json").write_text(_json.dumps({"agents": {
        "gm": {"tier": "T0"}, "pm-acme": {"tier": "T1", "role": "pm", "reports_to": "gm"}}}))
    folder = P._project_folder_for("qa-acme", "pm-acme")
    assert folder == tmp_path / "projects" / "acme" and folder.is_dir()
    from orchestra_cli import projects as PJ
    card = PJ.load(tmp_path)["acme"]
    assert card["pm"] == "pm-acme" and "qa-acme" in card["agents"]
    assert P._project_folder_for("helper", "gm") is None                  # a lead under the manager: the checkout
    plan = P.commission_plan("qa-acme", "hi", parent="pm-acme", cwd=folder)
    assert plan.env["AGENT_CWD"] == str(folder)
    assert "AGENT_CWD" not in P.commission_plan("helper", "hi", parent="gm").env
