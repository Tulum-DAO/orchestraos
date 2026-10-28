"""Bringing a dead agent back: resume reopens its last session (only when that session still exists); fresh
starts its next generation; auto resumes when it can, else starts fresh. Both return once Claude is up."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import revive_agent as rv  # noqa: E402
from scripts.test_briefing_successor import AG, SID, install  # noqa: E402,F401

DEAD = [{"id": AG, "name": "Builder", "alive": False}]


def test_resume_reopens_the_last_session_without_the_briefing_hold(install, monkeypatch):
    data, home = install
    monkeypatch.setenv("BRIEFING_HOLD_ROOT", "leftover")             # never carried into a resume
    seen = {}
    r = rv.resume(AG, data, directory=DEAD, spawn=lambda root, sid, env: seen.update(root=root, sid=sid, env=env),
                  session_up=lambda t: True, home=home)
    assert r == {"id": AG, "name": "Builder", "mode": "resume", "session_up": True}
    assert seen["sid"] == SID and "BRIEFING_HOLD_ROOT" not in seen["env"] and seen["env"]["AGENT_RUNTIME"] == "claude"


def test_resume_is_refused_without_a_session_to_resume(install, tmp_path):
    data, home = install
    with pytest.raises(rv.NotStartable, match="no session that can be resumed; start it fresh"):
        rv.resume(AG, data, directory=DEAD, spawn=lambda *a: None, session_up=lambda t: True, home=tmp_path / "nohome")


def test_auto_resumes_when_it_can_and_starts_fresh_when_it_cannot(install, monkeypatch, tmp_path):
    data, home = install
    picked = []
    monkeypatch.setattr(rv, "resume", lambda root, od, **k: picked.append("resume") or {"mode": "resume"})
    monkeypatch.setattr(rv.fs, "start", lambda root, **k: picked.append("fresh") or {"id": root, "session_up": True, "last_line": ""})
    assert rv.revive(AG, "auto", data, directory=DEAD, home=home)["mode"] == "resume"
    assert rv.revive(AG, "auto", data, directory=DEAD, home=tmp_path / "nohome")["mode"] == "fresh"
    assert picked == ["resume", "fresh"]
    with pytest.raises(ValueError):
        rv.revive(AG, "sideways", data)


def test_a_running_agent_is_never_revived(install):
    data, home = install
    with pytest.raises(rv.NotStartable, match="already running"):
        rv.resume(AG, data, directory=[{"id": AG, "name": AG, "alive": True}], spawn=lambda *a: None, home=home)


def test_a_retired_agent_resumes_in_the_folder_its_session_ran_in(install):
    data, home = install
    import json as _j
    tp = rv.briefing.transcript_path(AG, data, home)
    lines = tp.read_text().splitlines()
    first = _j.loads(lines[0]); first["cwd"] = "/work/demo.project"; lines[0] = _j.dumps(first)
    tp.write_text("\n".join(lines) + "\n")
    reg = _j.loads((data / "registry.json").read_text()); reg["agents"].pop(AG); (data / "registry.json").write_text(_j.dumps(reg))
    ses = _j.loads((data / "state" / "agent-sessions.json").read_text())
    ses[AG]["status"] = "retired"; (data / "state" / "agent-sessions.json").write_text(_j.dumps(ses))
    seen = {}
    rv.resume(AG, data, directory=DEAD, spawn=lambda root, sid, env: seen.update(env=env), session_up=lambda t: True, home=home)
    assert seen["env"]["AGENT_CWD"] == "/work/demo.project" and seen["env"]["AGENT_RUNTIME"] == "claude"
