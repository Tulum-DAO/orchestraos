"""The deployed form: jev_hook.py and jev_cli.py run as real subprocesses, offline."""
import json
import os
import subprocess
import sys

import pytest

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HOOK = os.path.join(SCRIPTS, "jev_hook.py")
CLI = os.path.join(SCRIPTS, "jev_cli.py")


@pytest.fixture
def env(tmp_path):
    """A child env with no keys at all, every store under tmp_path, and the real .env unreachable.

    PYTEST_CURRENT_TEST is dropped so the child runs the production selection path, not the
    test-isolation shortcut; with no key and no reachable .env it still cannot go live."""
    e = {k: v for k, v in os.environ.items()
         if not k.startswith("JEV_") and k not in ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY", "PYTEST_CURRENT_TEST")}
    e.update({"ORCHESTRA_DIR": str(tmp_path / "orch"), "JEV_DOTENV": str(tmp_path / "no.env"),
              "JEV_STATE_DIR": str(tmp_path / "state"), "JEV_LOG": str(tmp_path / "jev.jsonl"),
              "HOME": str(tmp_path / "home"), "CLAUDE_CODE_ENTRYPOINT": "cli"})
    return e


def run(argv, env, stdin=""):
    return subprocess.run([sys.executable] + argv, input=stdin, capture_output=True, text=True, env=env,
                          timeout=60, cwd=os.path.dirname(SCRIPTS))


def bash_event(cmd):
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd},
                       "cwd": "/tmp", "session_id": "s"})


def test_hook_refuses_the_mock_backend_by_default(env):
    env["JEV_BACKEND"] = "mock"
    p = run([HOOK, "pre-tool-use"], env, bash_event("rm -rf /var/lib/postgres/data"))
    assert p.returncode == 0 and p.stdout == ""


def test_hook_gates_on_mock_when_explicitly_allowed(env):
    env.update({"JEV_BACKEND": "mock", "JEV_HOOK_ALLOW_MOCK": "1"})
    p = run([HOOK, "pre-tool-use"], env, bash_event("rm -rf /var/lib/postgres/data"))
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert json.loads(open(env["JEV_LOG"]).read().splitlines()[-1])["gate"] == "A"


def test_hook_without_credentials_fails_open_and_logs(env):
    p = run([HOOK, "pre-tool-use"], env, bash_event("ls"))
    assert p.returncode == 0 and p.stdout == ""
    assert "JevConfigError" in open(env["JEV_LOG"]).read()


def test_hook_without_credentials_fails_closed_when_configured(env):
    env["JEV_GUARD_FAIL"] = "closed"
    p = run([HOOK, "pre-tool-use"], env, bash_event("ls"))
    assert json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_headless_run_without_credentials_fails_closed_by_default(env):
    """apr_e0fff9c7: an unattended sdk-cli run blocks when the guard cannot judge; an interactive seat
    in the same outage is let through; JEV_GUARD_FAIL_HEADLESS=open undoes it; explicit JEV_GUARD_FAIL wins."""
    env["CLAUDE_CODE_ENTRYPOINT"] = "sdk-cli"
    p = run([HOOK, "pre-tool-use"], env, bash_event("ls"))
    assert json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    env["JEV_GUARD_FAIL_HEADLESS"] = "open"
    assert run([HOOK, "pre-tool-use"], env, bash_event("ls")).stdout == ""
    del env["JEV_GUARD_FAIL_HEADLESS"]
    env["JEV_GUARD_FAIL"] = "open"
    assert run([HOOK, "pre-tool-use"], env, bash_event("ls")).stdout == ""
    env.pop("JEV_GUARD_FAIL")
    env["CLAUDE_CODE_ENTRYPOINT"] = "cli"
    assert run([HOOK, "pre-tool-use"], env, bash_event("ls")).stdout == ""


def test_kill_switches(env, tmp_path):
    env.update({"JEV_BACKEND": "mock", "JEV_HOOK_ALLOW_MOCK": "1", "JEV_GUARD_FAIL": "closed", "JEV_HOOKS_DISABLED": "1"})
    assert run([HOOK, "pre-tool-use"], env, bash_event("rm -rf /")).stdout == ""
    del env["JEV_HOOKS_DISABLED"]
    flag = tmp_path / "orch" / "state" / "jev" / "DISABLED"
    flag.parent.mkdir(parents=True)
    flag.write_text("")
    assert run([HOOK, "pre-tool-use"], env, bash_event("rm -rf /")).stdout == ""


def test_hook_bad_input(env):
    assert run([HOOK, "nonsense"], env).returncode == 1
    assert run([HOOK, "pre-tool-use"], env, "not json").returncode == 0


def test_hook_user_prompt_submit_reads_the_verdict_store(env, tmp_path):
    os.makedirs(env["JEV_STATE_DIR"])
    with open(os.path.join(env["JEV_STATE_DIR"], "sess.json"), "w") as fh:
        json.dump({"tier": "request", "message": "Please rotate.", "delivered": False}, fh)
    p = run([HOOK, "user-prompt-submit"], env, json.dumps({"session_id": "sess"}))
    assert json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"] == "[jev-compact] Please rotate."


def test_cli_files_and_file_on_mock(env):
    env["JEV_BACKEND"] = "mock"
    q = json.dumps({"kind": {"type": "choice", "instructions": "What is `content`?",
                             "criteria": {"transport": "http client endpoint retry", "other": None}}})
    p = run([CLI, "files", "scripts/jev/client.py", "scripts/jev/types.py", "--questions", q], env)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert [r["path"] for r in out["results"]] == ["scripts/jev/client.py", "scripts/jev/types.py"]
    p = run([CLI, "file", "scripts/jev/client.py", "--question", "Does `content` retry on 429?"], env)
    assert json.loads(p.stdout)["path"] == "scripts/jev/client.py"


def test_cli_reports_errors_on_stderr(env):
    env["JEV_BACKEND"] = "mock"
    p = run([CLI, "files", "x", "--questions", "{bad"], env)
    assert p.returncode == 1 and "not valid JSON" in json.loads(p.stderr)["error"]


def test_mcp_server_protocol(env):
    env["JEV_BACKEND"] = "mock"
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "ask_jev_file", "arguments": {
            "path": "scripts/jev/mock.py", "question": "Is `content` deterministic?"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "ask_jev", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "ask_jev", "arguments": {
            "state": "the build failed with a timeout", "questions_json": {"q": {"type": "noul", "instructions": "Is it flaky?"}}}}},
        {"jsonrpc": "2.0", "id": 6, "method": "nope"},
    ]
    p = run([CLI, "mcp"], env, "\n".join(json.dumps(m) for m in msgs) + "\nnot json\n")
    replies = [json.loads(x) for x in p.stdout.splitlines()]
    by_id = {r["id"]: r for r in replies}
    assert by_id[1]["result"]["serverInfo"]["name"] == "jev"
    assert {t["name"] for t in by_id[2]["result"]["tools"]} == {"ask_jev", "ask_jev_file", "ask_jev_files", "should_i_compact"}
    assert all("fn" not in t for t in by_id[2]["result"]["tools"])
    assert by_id[3]["result"]["isError"] is False and by_id[3]["result"]["structuredContent"]["path"] == "scripts/jev/mock.py"
    assert by_id[4]["result"]["isError"] and "questions_json" in by_id[4]["result"]["content"][0]["text"]
    assert by_id[5]["result"]["structuredContent"]["answers"]["q"]["type"] == "noul"
    assert by_id[6]["error"]["code"] == -32601
    assert by_id[None]["error"]["code"] == -32700
    assert len(replies) == 7  # the notification got no reply


# ---------------------------------------------------------------- rollout switchboard

def _hook_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("jev_hook_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resolve_gates(monkeypatch):
    h = _hook_module()
    monkeypatch.delenv("JEV_GUARD_GATES", raising=False)
    cfg = {"gates": ["C"], "canary_gates": ["A", "B"], "canary_seats": ["jev-builder"]}
    assert h.resolve_gates({}, lambda: "x") == "A,B,C"
    assert h.resolve_gates(cfg, lambda: "gm") == "C"
    assert h.resolve_gates(cfg, lambda: "jev-builder") == "A,B,C"
    assert h.resolve_gates({**cfg, "canary_seats": [""]}, lambda: "") == "C", "outside tmux is never a canary"
    assert h.resolve_gates({"gates": []}, lambda: "x") == ""
    monkeypatch.setenv("JEV_GUARD_GATES", "A")
    assert h.resolve_gates(cfg, lambda: "gm") == "A"


def write_config(env, cfg):
    path = os.path.join(env["ORCHESTRA_DIR"], "state", "jev", "config.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(cfg if isinstance(cfg, str) else json.dumps(cfg))


def test_config_screen_only_does_not_gate_bash(env):
    env.update({"JEV_BACKEND": "mock", "JEV_HOOK_ALLOW_MOCK": "1"})
    env.pop("TMUX_PANE", None)
    write_config(env, {"gates": ["C"], "canary_gates": ["A", "B"], "canary_seats": ["jev-builder"]})
    assert run([HOOK, "pre-tool-use"], env, bash_event("rm -rf /var/lib/postgres/data")).stdout == ""
    write_config(env, {"gates": ["A", "C"]})
    assert "deny" in run([HOOK, "pre-tool-use"], env, bash_event("rm -rf /var/lib/postgres/data")).stdout


def test_config_compact_off_and_malformed(env):
    os.makedirs(env["JEV_STATE_DIR"])
    with open(os.path.join(env["JEV_STATE_DIR"], "sess.json"), "w") as fh:
        json.dump({"tier": "request", "message": "Please rotate.", "delivered": False}, fh)
    write_config(env, {"compact": False})
    assert run([HOOK, "user-prompt-submit"], env, json.dumps({"session_id": "sess"})).stdout == ""
    write_config(env, "{not json")
    p = run([HOOK, "user-prompt-submit"], env, json.dumps({"session_id": "sess"}))
    assert p.returncode == 1 and p.returncode != 2, "a broken switchboard is a loud NON-blocking error"


def test_config_write_roots_extend_gate_b(env, tmp_path):
    env.update({"JEV_BACKEND": "mock", "JEV_HOOK_ALLOW_MOCK": "1"})
    env.pop("TMUX_PANE", None)
    worktree = tmp_path / "worktrees" / "seat-wt"
    worktree.mkdir(parents=True)
    ev = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(worktree / "a.py"), "content": "print(1)\n"},
                     "cwd": str(tmp_path / "orch"), "session_id": "s"})
    write_config(env, {"gates": ["B"]})
    assert "outside the workspace roots" in run([HOOK, "pre-tool-use"], env, ev).stdout
    write_config(env, {"gates": ["B"], "write_roots": [str(tmp_path / "worktrees")]})
    assert "outside the workspace roots" not in run([HOOK, "pre-tool-use"], env, ev).stdout


def test_without_orchestra_dir_the_data_dir_is_home_dot_orchestra_not_the_checkout(env, tmp_path, monkeypatch):
    """The public tree runs from its own checkout; state, logs and .env live in ~/.orchestra. With no
    ORCHESTRA_DIR the hook must read ~/.orchestra/state/jev/config.json and log under ~/.orchestra/logs,
    exactly where the private tree's hook reads and writes today (same files, by symlink)."""
    home = tmp_path / "home"
    orch = home / ".orchestra"
    for k in ("ORCHESTRA_DIR", "JEV_LOG", "JEV_STATE_DIR", "JEV_DOTENV"):
        env.pop(k)
    env.update({"JEV_BACKEND": "mock", "JEV_HOOK_ALLOW_MOCK": "1", "HOME": str(home)})
    env.pop("TMUX_PANE", None)
    (orch / "state" / "jev").mkdir(parents=True)
    (orch / "state" / "jev" / "config.json").write_text(json.dumps({"gates": ["A"]}))
    assert "deny" in run([HOOK, "pre-tool-use"], env, bash_event("rm -rf /var/lib/postgres/data")).stdout
    assert (orch / "logs" / "jev-decisions.jsonl").exists(), "decision log goes to ~/.orchestra/logs"
    (orch / "state" / "jev" / "DISABLED").write_text("")
    assert run([HOOK, "pre-tool-use"], env, bash_event("rm -rf /var/lib/postgres/data")).stdout == ""

    from jev import client, compact
    for k in ("ORCHESTRA_DIR", "JEV_DOTENV", "JEV_STATE_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(home))
    assert client.dotenv_path() == str(orch / ".env")
    assert compact.state_dir() == str(orch / "state" / "jev" / "compact")
