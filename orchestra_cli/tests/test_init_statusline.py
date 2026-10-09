"""`orchestra init` and the opt-in status line: never replaces a user's own line silently, and doctor
says plainly what an operator gets in each state."""
import json
import sys
from pathlib import Path

from orchestra_cli import doctor as D
from orchestra_cli import init_cmd as I
from orchestra_cli.tests.test_init import Runner, _hooks_repo

USER_LINE = {"type": "command", "command": "echo mine", "padding": 0}


def _init(tmp_path, monkeypatch, settings=None, **kw):
    root = _hooks_repo(tmp_path)
    cfg = tmp_path / "claude-cfg"
    cfg.mkdir(exist_ok=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("ORCHESTRA_YES", raising=False)
    sp = cfg / "settings.json"
    if settings is not None:
        sp.write_text(json.dumps(settings, indent=2) + "\n")
    before = sp.read_bytes() if sp.exists() else None
    report = I.run_init(root, data_dir=tmp_path / "data", run=Runner(), skip_npm=True, skip_venv=True, **kw)
    return {r.step: r for r in report}["statusline"], sp, before, report


def _hooks():
    sys.path.insert(0, str(Path(I.__file__).resolve().parent.parent / "hooks"))
    import install
    return install


def test_yes_installs_the_status_line_when_there_is_none(tmp_path, monkeypatch):
    step, sp, _, _ = _init(tmp_path, monkeypatch, yes=True, interactive=False)
    assert step.did and "installed" in step.detail
    assert _hooks().statusline_status(settings_path=sp)["state"] == "installed"


def test_yes_never_replaces_or_wraps_the_users_own_status_line(tmp_path, monkeypatch):
    step, sp, before, report = _init(tmp_path, monkeypatch, settings={"statusLine": USER_LINE}, yes=True,
                                     interactive=False)
    assert not step.did and "kept your own status line" in step.detail and "stay empty" in step.detail
    assert json.loads(sp.read_text())["statusLine"] == USER_LINE
    assert step not in I.failed_steps(report), "declining is not a failure"


def test_asked_with_their_command_shown_and_a_yes_chains(tmp_path, monkeypatch):
    asked = []
    step, sp, _, _ = _init(tmp_path, monkeypatch, settings={"statusLine": USER_LINE},
                           confirm=lambda _p: True, confirm_statusline=lambda p: asked.append(p) or True)
    assert step.did and "chained" in step.detail
    assert len(asked) == 1 and "echo mine" in asked[0] and "--remove" in asked[0]
    assert _hooks().statusline_status(settings_path=sp) == {"state": "chained", "original": USER_LINE}


def test_a_no_to_the_chain_question_writes_nothing(tmp_path, monkeypatch):
    step, sp, before, _ = _init(tmp_path, monkeypatch, settings={"statusLine": USER_LINE},
                                confirm=lambda _p: False, confirm_statusline=lambda _p: False)
    assert not step.did and "declined" in step.detail
    assert sp.read_bytes() == before


def test_not_a_terminal_and_no_yes_skips(tmp_path, monkeypatch):
    step, sp, _, _ = _init(tmp_path, monkeypatch, interactive=False)
    assert not step.did and "skipped" in step.detail and not sp.exists()


def test_doctor_names_each_state_plainly(tmp_path):
    H = _hooks()
    sp = tmp_path / "settings.json"
    sp.write_text("{}")
    c = D.statusline_check(H, sp)
    assert c.status == D.WARN and "not installed" in c.detail and "app context numbers will stay empty" in c.detail
    sp.write_text(json.dumps({"statusLine": USER_LINE}))
    c = D.statusline_check(H, sp)
    assert c.status == D.WARN and "declined" in c.detail and "app context numbers will stay empty" in c.detail
    repo = Path(I.__file__).resolve().parent.parent
    sp.write_text(json.dumps({"statusLine": {"type": "command", "command": H._statusline_command(repo, None)}}))
    assert D.statusline_check(H, sp).status == D.OK and "installed" in D.statusline_check(H, sp).detail
    sp.write_text(json.dumps({"statusLine": dict(USER_LINE, command=H._statusline_command(repo, USER_LINE))}))
    c = D.statusline_check(H, sp)
    assert c.status == D.OK and "chained" in c.detail
    assert all(not x.required for x in (c,)), "advisory: OrchestraOS runs without it"
