"""The deriver kill file: while <data dir>/state/DERIVER_STATUS_DISABLED exists, the telemetry deriver is never the
status source; the screen + hook detector decides (operator order 2026-09-19, re-raised 2026-11-25 when every working
seat read IDLE in the agent rail because a fresh status.json said so).

The public agent-status.py had no such check, so the gateway, which imports it, took the deriver's verdict whenever
status.json was under DERIVER_FRESH_S old. RED first: with the file present and a fresh deriver row saying idle, a
pane whose hook says working (a real live turn, the fixture screen) must read working with confidence hook. The
control without the file proves the deriver path itself is untouched.

Hermetic: tmp HOME / state / events dirs; the process, pane and deriver snapshot are stubbed; the kill-file path
points into tmp.
"""
import importlib.util
import json
import os
import time as _time

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FIXTURE = os.path.join(_HERE, "fixtures", "esc-auq", "post_esc_idle_v2.1.284.ansi")


@pytest.fixture(scope="module")
def A():
    spec = importlib.util.spec_from_file_location("agent_status_kf", os.path.join(_HERE, "agent-status.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _user_text(text):
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def _assistant(text="ok"):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _wire(A, monkeypatch, tmp_path, *, deriver_status, kill_file):
    """A live seat mid-turn (hook PreToolUse working, transcript live, the real post-Esc screen fixture, which the
    hook rescues to working) with a FRESH deriver row that says `deriver_status`; the kill file present or not."""
    home = tmp_path / "home"
    cwd = "/home/user/repos/demo"
    slug = A._project_slug(cwd)
    proj = home / ".claude" / "projects" / slug
    proj.mkdir(parents=True)
    sid = "sess-kf"
    (proj / f"{sid}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in [_user_text("do a thing"), _assistant()]) + "\n")
    with open(_FIXTURE, encoding="utf-8", errors="replace") as f:
        screen = f.read()
    monkeypatch.setattr(A, "check_claude_process",
                        lambda s: {"running": True, "cpu": 1.0, "pid": 1234, "rss_mb": 100, "elapsed": "01:00"})
    monkeypatch.setattr(A, "get_tmux_output", lambda s, *a, **k: screen)
    monkeypatch.setattr(A, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(A, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(A, "get_pane_id", lambda s: "%9")
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(home) if p == "~" else p)
    with open(os.path.join(str(tmp_path), "9.json"), "w") as f:
        json.dump({"event": "PreToolUse", "state": "working", "ts": _time.time(), "tool": "Bash",
                   "cwd": cwd, "session_id": sid}, f)
    now = _time.time()
    monkeypatch.setattr(A, "_read_deriver_snapshot",
                        lambda: {"seats": {"kf-sess": {"status": deriver_status, "ts": now}}})
    state_dir = tmp_path / "data" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    kf = state_dir / "DERIVER_STATUS_DISABLED"
    monkeypatch.setattr(A, "DERIVER_STATUS_KILL_FILE", str(kf))
    if kill_file:
        kf.write_text("operator order 2026-09-19\n")
    return A.get_agent_status("kf-sess")


def test_with_the_kill_file_a_fresh_deriver_idle_never_becomes_the_status(A, monkeypatch, tmp_path):
    st = _wire(A, monkeypatch, tmp_path, deriver_status="idle", kill_file=True)
    assert st["state"] == "working", st
    assert st["confidence"] == "hook"
    assert st.get("deriver_state") == "idle" and st.get("deriver_status_disabled") is True   # information, not the verdict
    assert isinstance(st.get("deriver_age_s"), int)


def test_CONTROL_without_the_kill_file_the_fresh_deriver_is_the_status_as_before(A, monkeypatch, tmp_path):
    st = _wire(A, monkeypatch, tmp_path, deriver_status="idle", kill_file=False)
    assert (st["state"], st["confidence"], st["deriver_state"]) == ("idle", "deriver", "idle")
    assert "deriver_status_disabled" not in st


def test_the_file_is_read_per_call_so_a_long_lived_importer_honours_it_without_a_restart(A, monkeypatch, tmp_path):
    st = _wire(A, monkeypatch, tmp_path, deriver_status="stalled", kill_file=False)
    assert st["state"] == "stalled" and st["confidence"] == "deriver"
    open(A.DERIVER_STATUS_KILL_FILE, "w").write("now\n")           # the operator drops the file: next call obeys
    st = A.get_agent_status("kf-sess")
    assert st["state"] == "working" and st["confidence"] == "hook" and st["deriver_state"] == "stalled"
    os.remove(A.DERIVER_STATUS_KILL_FILE)                            # and lifts it: next call trusts the deriver again
    assert A.get_agent_status("kf-sess")["confidence"] == "deriver"


def test_deriver_false_still_skips_the_deriver_entirely(A, monkeypatch, tmp_path):
    _wire(A, monkeypatch, tmp_path, deriver_status="idle", kill_file=False)
    st = A.get_agent_status("kf-sess", deriver=False)
    assert st["confidence"] != "deriver" and "deriver_state" not in st


def test_the_kill_file_lives_under_the_data_dir_state(A):
    assert A.DERIVER_STATUS_KILL_FILE == os.path.join(A.ORCH_DIR, "state", "DERIVER_STATUS_DISABLED")
    assert A.deriver_status_disabled() in (True, False)
