# RED-first: "commission an agent to X" through Arturo's spawn_agent tool must produce a REAL
# seat (the repo's spawn-agent.sh, runtime-declared, registered) plus a durable msg_store
# commission row — not a bare `tmux new-session 'claude'` and a Telegram text (T2 acceptance).
import importlib.util
import pathlib

import pytest


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_commission", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setenv("ARTURO_GM_INJECT", "0")
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "0")
    monkeypatch.setenv("ORCHESTRA_ARTURO_BRAIN", "api")     # no runtime probe at import
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return _load_proxy()


def test_commission_plan_uses_spawn_script_with_runtime_and_task(mod):
    plan = mod.commission_plan("docs-dev", "write the README", runtime="codex", repo_root=pathlib.Path("/r"))
    assert plan.argv[:2] == ["bash", "/r/spawn-agent.sh"]
    assert plan.argv[2] == "docs-dev" and "--task" in plan.argv
    assert plan.argv[plan.argv.index("--task") + 1] == "write the README"
    assert plan.env["AGENT_RUNTIME"] == "codex"
    assert plan.env["AGENT_MODEL"]                       # writer refuses a row with neither


def test_commission_plan_defaults_runtime_to_brain_runtime(mod):
    mod.brain = mod._brain.RuntimeBrain("gemini", "agy", runner=lambda s, t: "")
    plan = mod.commission_plan("x", "y", runtime=None, repo_root=pathlib.Path("/r"))
    assert plan.env["AGENT_RUNTIME"] == "gemini"


def test_commission_plan_falls_back_to_claude_for_api_brain(mod):
    mod.brain = mod._brain.NullBrain("none")
    plan = mod.commission_plan("x", "y", runtime=None, repo_root=pathlib.Path("/r"))
    assert plan.env["AGENT_RUNTIME"] == "claude"


def test_spawn_agent_tool_runs_plan_and_files_msg_store_row(mod, monkeypatch, tmp_path):
    ran = {}
    sent = {}

    def fake_run(plan, timeout):
        ran["plan"] = plan
        return True, "[spawn] docs-dev ready"

    class _Store:
        def send(self, **kw):
            sent.update(kw)
            return "msg_abc123"

    monkeypatch.setattr(mod, "_run_commission", fake_run)
    monkeypatch.setattr(mod, "_message_store", lambda: _Store())
    monkeypatch.setattr(mod, "run_local", lambda cmd, timeout=15: (True, ""))   # tmux has-session
    monkeypatch.setattr(mod, "record_spawned_session", lambda s: None)
    monkeypatch.setattr(mod, "_notify_spawned", lambda *a, **k: None)

    out = mod.execute_tool("spawn_agent", {"session_name": "docs-dev", "machine": "vps",
                                           "task": "write the README"})
    assert "CONFIRMED" in out and "docs-dev" in out
    assert ran["plan"].argv[2] == "docs-dev"
    assert sent["to_agent"] == "docs-dev" and sent["from_agent"] == "arturo"
    assert sent["type"] == "task" and "write the README" in sent["body"]
    assert "msg_abc123" in out


def test_spawn_agent_tool_failure_is_a_sentence_not_an_exception(mod, monkeypatch):
    monkeypatch.setattr(mod, "_run_commission", lambda plan, timeout: (False, "REFUSE: no runtime"))
    out = mod.execute_tool("spawn_agent", {"session_name": "bad", "machine": "vps", "task": "t"})
    assert out.startswith("FAILED") and "REFUSE" in out


# ---- /text route: the web/iOS home text path -----------------------------------------------

def test_text_route_loopback_only(mod):
    c = mod.app.test_client()
    assert c.post("/text", json={"text": "hi"}, environ_base={"REMOTE_ADDR": "10.0.0.9"}).status_code == 403


def test_text_route_null_brain_answers_with_the_fix_not_500(mod, monkeypatch):
    mod.brain = mod._brain.NullBrain("no key, no cli")
    monkeypatch.setattr(mod, "build_context", lambda calling_channel="text": "SYS")
    c = mod.app.test_client()
    r = c.post("/text", json={"text": "hello", "conversation_id": "c1"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j["ok"] and "no key, no cli" in j["reply_text"] and j["brain"]["kind"] == "none"
    assert j["conversation_id"] == "c1"


def test_text_route_threads_history_per_conversation(mod, monkeypatch):
    seen = []

    def fake_complete(messages, **kw):
        seen.append([m["role"] for m in messages])
        return mod._brain.make_response("ok", None, "stop")
    mod.brain = mod._brain.RuntimeBrain("claude", "claude", runner=lambda s, t: "ok")
    monkeypatch.setattr(mod.brain, "complete", fake_complete)
    monkeypatch.setattr(mod, "build_context", lambda calling_channel="text": "SYS")
    c = mod.app.test_client()
    c.post("/text", json={"text": "one", "conversation_id": "h1"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    c.post("/text", json={"text": "two", "conversation_id": "h1"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert seen[0] == ["system", "user"]
    assert seen[1] == ["system", "user", "assistant", "user"]


def test_text_route_empty_is_400(mod):
    c = mod.app.test_client()
    r = c.post("/text", json={"text": "  "}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 400


def test_health_reports_brain_and_mode(mod):
    j = mod.app.test_client().get("/health").get_json()
    assert j["brain"]["kind"] in ("api", "runtime", "none") and j["mode"] in ("voice", "text-only")
    assert "brain_mode" in j and isinstance(j["voice"], bool)
