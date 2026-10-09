"""list_agents says RUNNING only when the status detector sees an agent CLI, the same truth the
dashboard reads. Found in a new-user test: Arturo said gm was alive while the Agents page showed a
red dot, because list_agents counted any tmux session as RUNNING (a session can hold a shell or a
nested tmux client and no agent)."""
import importlib.util
import pathlib

from services.arturo.conftest import as_fleet


def _load_proxy():
    spec = importlib.util.spec_from_file_location("arturo_proxy_list_agents",
                                                  pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return as_fleet(mod)


H = [{"agent_id": "gm", "role": "manager", "project": "fleet", "always_on": True},
     {"agent_id": "pm-x", "role": "pm", "project": "x"},
     {"agent_id": "dev-x", "role": "dev", "project": "x"}]


def test_a_session_without_an_agent_is_not_running():
    mod = _load_proxy()
    states = {"gm": "stopped", "pm-x": "idle"}
    out = mod._list_agents_text(H, {"gm", "pm-x"}, set(), detect=states.get)
    gm = next(l for l in out.splitlines() if l.strip().startswith("gm:"))
    assert "RUNNING" not in gm and "no agent" in gm
    pm = next(l for l in out.splitlines() if l.strip().startswith("pm-x:"))
    assert "RUNNING" in pm
    dev = next(l for l in out.splitlines() if l.strip().startswith("dev-x:"))
    assert "RUNNING" not in dev and "no session" in dev


def test_a_detector_that_cannot_answer_is_could_not_tell_never_running():
    mod = _load_proxy()
    out = mod._list_agents_text(H[:1], {"gm"}, set(), detect=lambda n: "unknown")
    assert "RUNNING" not in out and "could not tell" in out


def test_the_running_count_counts_agents_not_sessions():
    mod = _load_proxy()
    states = {"gm": "stopped", "pm-x": "working"}
    out = mod._list_agents_text(H, {"gm", "pm-x", "scratch"}, set(), detect=states.get)
    assert out.startswith("Agents (3 registered, 1 running)")
    assert "Unregistered sessions: scratch" in out
