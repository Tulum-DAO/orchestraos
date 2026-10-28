"""Arturo's agent tools take a person's words: an exact id, name or old name runs (named by its id); one close
agent is assumed and said; several are asked about; a dead one is offered revival; an unknown name is offered
as a new agent. A live tmux session named exactly passes as before; a resolver failure never blocks a tool."""
import importlib.util
import pathlib

import pytest

DIR = [
    {"id": "arturo", "name": "Example", "aliases": [], "alive": True, "builtin": True},
    {"id": "pm-demo", "name": "Demo", "aliases": ["Former"], "alive": True, "resumable": True},
    {"id": "dev-demo", "name": "dev-demo", "aliases": [], "alive": True},
    {"id": "qa-demo", "name": "qa-demo", "aliases": [], "alive": False, "resumable": True},
    {"id": "inspiration", "name": "Muse", "aliases": [], "alive": False},
]


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("arturo_proxy_seatnames", pathlib.Path("services/arturo/arturo-proxy.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def run(mod, monkeypatch):
    from scripts.identity_store import seat_resolve as sr
    monkeypatch.setattr(sr, "load_directory", lambda *a, **k: DIR)
    monkeypatch.setattr(sr, "_live_tmux_sessions", lambda: {"pm-demo", "dev-demo", "orchestra-supervisor"})
    seen = []
    monkeypatch.setattr(mod, "_execute_tool_raw", lambda name, args, user_turns=None: seen.append((name, dict(args))) or f"ran {name}")
    monkeypatch.setattr(mod, "_watch_agent_work", lambda name, args, out: out)
    return lambda name, **args: (mod._execute_tool_impl(name, args), seen[-1][1] if seen else None)


def test_an_exact_id_or_a_live_session_name_runs_unchanged(run):
    out, args = run("get_agent_output", session_name="pm-demo")
    assert out == "ran get_agent_output" and args["session_name"] == "pm-demo"
    out, args = run("inject_message", session_name="orchestra-supervisor", message="hi")   # not an agent, but live
    assert args["session_name"] == "orchestra-supervisor"


def test_a_name_or_old_name_runs_on_the_id_and_says_who_it_is(run):
    out, args = run("agent_message", to_agent="Former", subject="status")
    assert args["to_agent"] == "pm-demo" and out.startswith("(Former is Demo (pm-demo).)")
    out, args = run("agent_message", to_agent="Example", subject="note to self")
    assert args["to_agent"] == "arturo"


def test_one_close_agent_is_assumed_and_said(run):
    out, args = run("agent_message", to_agent="deno", subject="status")
    assert args["to_agent"] == "pm-demo" and "the only close match is Demo (pm-demo)" in out


def test_several_close_agents_are_asked_about(run, mod):
    out, args = run("agent_message", to_agent="demo dev", subject="status")
    assert args["to_agent"] == "dev-demo"                      # same words, any order: exact for a person
    out, _ = run("kill_agent", session_name="qa")
    assert out.startswith("NO SUCH AGENT")
    DIR.append({"id": "dev-other", "name": "dev-other", "aliases": [], "alive": True})
    try:
        out, _ = run("agent_message", to_agent="dev", subject="x")
        assert out.startswith("NOT RUN: 'dev' could mean more than one agent") and "ask_choices" in out
    finally:
        DIR.pop()


def test_a_dead_agent_is_offered_revival_and_reading_it_still_works(run):
    out, _ = run("agent_message", to_agent="qa-demo", subject="x")
    assert out.startswith("NOT SENT: qa-demo exists but is not running") and "revive='resume' or revive='fresh'" in out
    out, _ = run("kill_agent", session_name="Muse")
    assert "is not running already" in out
    out, args = run("read_agent_conversation", agent_id="Muse")
    assert args["agent_id"] == "inspiration"                        # reading a dead agent's mail is fine


def test_spawn_offers_revival_for_an_existing_dead_agent_and_refuses_a_running_one(run, monkeypatch):
    from scripts import fresh_successor
    started = []
    monkeypatch.setattr(fresh_successor, "start", lambda root, **k: started.append(root) or
                        {"id": root, "name": "Muse", "session_up": True, "last_line": "It was drafting a poem."})
    out, _ = run("spawn_agent", session_name="dev-demo", machine="vps")
    assert out.startswith("NOT RUN: dev-demo already exists and is running")
    out, _ = run("spawn_agent", session_name="muse", machine="vps")
    assert out.startswith("ASK FIRST: Muse (inspiration) exists but is not running") and "resume" not in out   # nothing to resume
    out, args = run("spawn_agent", session_name="muse", machine="vps", revive="fresh")
    assert out.startswith("Muse (inspiration) is back as its next generation") and "holds its replies" in out
    assert started == ["inspiration"]                               # the fast successor path, under its id
    out, args = run("spawn_agent", session_name="scout", machine="vps")
    assert args["session_name"] == "scout" and out == "ran spawn_agent"   # a new agent


def test_a_resolver_failure_never_blocks_a_tool(run, monkeypatch):
    from scripts.identity_store import seat_resolve as sr
    monkeypatch.setattr(sr, "load_directory", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    out, args = run("agent_message", to_agent="Former", subject="x")
    assert out == "ran agent_message" and args["to_agent"] == "Former"


def test_an_install_with_no_readable_agents_never_blocks_a_tool(run, monkeypatch):
    from scripts.identity_store import seat_resolve as sr
    monkeypatch.setattr(sr, "load_directory", lambda *a, **k: [d for d in DIR if d.get("builtin")])
    out, args = run("agent_message", to_agent="anyone", subject="x")
    assert out == "ran agent_message" and args["to_agent"] == "anyone"


def test_resume_reopens_the_last_session_when_it_can_and_says_so_when_it_cannot(run, monkeypatch):
    from scripts import revive_agent
    calls = []
    monkeypatch.setattr(revive_agent, "revive", lambda root, mode, **k: calls.append((root, mode)) or
                        {"id": root, "name": root, "mode": mode, "session_up": True, "last_line": ""})
    out, _ = run("spawn_agent", session_name="qa-demo", machine="vps", revive="resume")
    assert calls == [("qa-demo", "resume")] and out.startswith("qa-demo is back in its last session, where it left off")
    out, _ = run("spawn_agent", session_name="Muse", machine="vps", revive="resume")       # no resumable session
    assert out.startswith("ASK FIRST: Muse (inspiration) has no session that can be resumed") and "revive='fresh'" in out
    assert calls == [("qa-demo", "resume")]


def test_a_crashed_agents_leftover_session_still_gets_the_revive_offer(run, monkeypatch):
    from scripts.identity_store import seat_resolve as sr
    monkeypatch.setattr(sr, "_live_tmux_sessions", lambda: {"qa-demo", "orchestra-supervisor"})   # qa-demo: tmux up, Claude dead
    out, _ = run("agent_message", to_agent="qa-demo", subject="hello")
    assert out.startswith("NOT SENT: qa-demo exists but is not running")
    out, args = run("inject_message", session_name="orchestra-supervisor", message="hi")   # not an agent: as before
    assert args["session_name"] == "orchestra-supervisor"


def test_a_name_two_projects_share_goes_to_the_project_being_discussed(run, mod, monkeypatch):
    from scripts.identity_store import seat_resolve as sr
    p1, p2 = {"slug": "demo", "label": "Demo"}, {"slug": "other", "label": "Other"}
    two = [{"id": "dev-demo", "name": "dev", "aliases": [], "alive": True, "project": p1, "full_name": "Demo · dev"},
           {"id": "dev-other", "name": "dev", "aliases": [], "alive": True, "project": p2, "full_name": "Other · dev"}]
    monkeypatch.setattr(sr, "load_directory", lambda *a, **k: two)
    monkeypatch.setattr(mod, "_current_thread", lambda: "web_t1")
    mod._THREAD_PROJECT.pop("web_t1", None)
    out, _ = run("agent_message", to_agent="dev", subject="x")
    assert out.startswith("NOT RUN: 'dev' could mean more than one agent") and "Demo · dev (dev-demo)" in out
    mod._note_page_project("web_t1", {"route": "/agent/dev-other", "entityKind": "agent", "entityId": "dev-other"})
    monkeypatch.setattr(mod, "_project_of_agent", lambda a: "other")
    mod._note_page_project("web_t1", {"route": "/agent/dev-other"})
    out, args = run("agent_message", to_agent="dev", subject="x")
    assert args["to_agent"] == "dev-other" and "the only close match is Other · dev (dev-other)" in out
