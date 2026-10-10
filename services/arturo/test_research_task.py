"""RED-first (gm audit 2026-10-09 #2): `research` built spawn_agent's tool_args with "prompt", but
spawn_agent reads only args["task"], so every research seat spawned with NO question. Drive the
REAL research -> async_task -> spawn_agent chain and check what spawn_agent actually receives."""
import importlib.util
import pathlib
import threading
import time


def test_research_hands_its_query_to_spawn_agent_as_task(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_research", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    got = []
    done = threading.Event()
    orig = mod.execute_tool

    def ex(name, args, user_turns=None):
        if name == "spawn_agent":
            got.append(dict(args))
            done.set()
            return "spawned"
        return orig(name, args, user_turns)
    monkeypatch.setattr(mod, "execute_tool", ex)
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(mod._TG_OUTBOX, "allow", lambda text: (True, ""))
    # public: the task used to embed the bot token in a curl URL. A sentinel proves it no longer does.
    monkeypatch.setattr(mod, "TELEGRAM_BOT_TOKEN", "123456:SENTINEL-not-a-real-token")
    tok = mod._TEAM_TURN.set({"principal": "fleet"}) if hasattr(mod, "_TEAM_TURN") else None
    try:
        mod.execute_tool("research", {"query": "best lavalier mic for iPhone interviews"})
        assert done.wait(5), "spawn_agent never ran"
    finally:
        if tok is not None:
            mod._TEAM_TURN.reset(tok)
    task = got[0].get("task", "")
    assert "best lavalier mic for iPhone interviews" in task, got[0]
    assert got[0]["session_name"].startswith("research-")
    assert "SENTINEL" not in task and "api.telegram.org" not in task, "the bot token must never reach the agent"
    assert "tg-notify.sh" in task, "the agent reports through the by-reference notifier"
