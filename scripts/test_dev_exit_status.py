"""Back to chat asks whether the TUI is busy from what it shows and what its hooks said, never from the process
deriver. A TUI that has just started (Dev on) reads busy on the deriver for its first ~20 s while it loads the
resumed session and starts its servers, though it sits at an idle prompt: that refused every other Back to chat
(409 "busy in the terminal"). The real detector, a real idle screen fixture, a stood-in deriver snapshot."""
import asyncio
import importlib.util
import json
import os
import time
import types

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
from test_agent_status_interrupt import _assistant, _post_esc_screen, _user_text, _with_typed  # noqa: E402


@pytest.fixture
def A():
    spec = importlib.util.spec_from_file_location("agent_status_dx", os.path.join(_HERE, "agent-status.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _wire(A, monkeypatch, tmp_path, *, deriver_status, hook_event, hook_state, tail, screen):
    home = tmp_path / "home"
    cwd = "/home/user/repos/demo"
    proj = home / ".claude" / "projects" / A._project_slug(cwd)
    proj.mkdir(parents=True)
    sid = "sess-dx"
    (proj / f"{sid}.jsonl").write_text("\n".join(json.dumps(e) for e in tail) + "\n")
    monkeypatch.setattr(A, "check_claude_process", lambda s: {"running": True, "cpu": 30.0, "pid": 1234,
                                                              "rss_mb": 300, "elapsed": "00:13"})
    monkeypatch.setattr(A, "get_tmux_output", lambda s, *a, **k: screen)
    monkeypatch.setattr(A, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(A, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(A, "get_pane_id", lambda s: "%9")
    monkeypatch.setattr(A, "_headless_seat", lambda s: None)          # in Dev: a TUI seat
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(home) if p == "~" else p)
    monkeypatch.setattr(A, "_read_deriver_snapshot", lambda: {"seats": {"dev-demo": {
        "ts": time.time(), "status": deriver_status}}})
    with open(os.path.join(str(tmp_path), "9.json"), "w") as f:
        json.dump({"event": hook_event, "state": hook_state, "ts": time.time() - 13, "tool": "",
                   "cwd": cwd, "session_id": sid}, f)


@pytest.mark.parametrize("deriver_status", ["computing", "streaming"])
def test_a_tui_that_just_started_at_an_idle_prompt_is_not_busy(A, monkeypatch, tmp_path, deriver_status):
    _wire(A, monkeypatch, tmp_path, deriver_status=deriver_status, hook_event="SessionStart", hook_state="idle",
          tail=[_user_text("Test only: reply with OK."), _assistant("OK")], screen=_post_esc_screen())
    assert A.get_agent_status("dev-demo")["state"] == "working"          # the deriver's view: what refused it
    st = A.get_agent_status("dev-demo", deriver=False)
    assert st["state"] == "idle", st


def test_a_turn_running_in_the_tui_still_blocks(A, monkeypatch, tmp_path):
    _wire(A, monkeypatch, tmp_path, deriver_status="computing", hook_event="UserPromptSubmit", hook_state="working",
          tail=[_user_text("do a thing")], screen=_post_esc_screen())
    assert A.get_agent_status("dev-demo", deriver=False)["state"] == "working"


def test_a_typed_draft_in_the_tui_still_blocks(A, monkeypatch, tmp_path):
    _wire(A, monkeypatch, tmp_path, deriver_status="idle", hook_event="SessionStart", hook_state="idle",
          tail=[_assistant("OK")], screen=_with_typed(_post_esc_screen(), "half a sentence"))
    st = A.get_agent_status("dev-demo", deriver=False)
    assert st["state"] in ("stranded_input", "queued_input", "typing"), st


def test_back_to_chat_asks_without_the_deriver(monkeypatch, tmp_path):
    import sys
    sys.path.insert(0, _HERE)
    import headless_switch as HS
    import watch_gateway as G
    asked = []
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(G, "_protected_refusal", lambda r, s: None)
    monkeypatch.setattr(G, "_authorized", lambda r: True)
    monkeypatch.setattr(HS, "headless_seat", lambda s, orch_dir=None, include_dev=False, runtime=None: s)
    monkeypatch.setattr(HS, "in_dev", lambda seat, orch_dir=None: True)
    monkeypatch.setattr(HS, "request", lambda seat, payload, **kw: {"ok": True, "exit_ms": 1})
    monkeypatch.setattr(G, "_agent_status", lambda: types.SimpleNamespace(
        get_agent_status=lambda s, **k: asked.append(k) or {"state": "idle"}))

    class Req:
        headers = {}

        async def json(self):
            return {"session": "dev-demo", "on": False}
    r = asyncio.run(G.handle_agent_dev(Req()))
    assert r.status == 200 and asked == [{"deriver": False}]
