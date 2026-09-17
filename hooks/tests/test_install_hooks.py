"""RED-first: `orchestra init` installs the shipped hooks into the user's Claude settings
(merge, never clobber; idempotent; user's own hooks untouched; data dir baked in as env).

Tier 0 item 1 (gm msg_906f4d83): today the idle-inbox drain, the pane-state hook and the
rotation self-trigger live only in the operator's ~/.claude/settings.json, so a clean
install has seats that never act on mail without a keypress.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "hooks"))
import install as H  # noqa: E402


def _events(settings):
    return {ev: [hk["command"] for rule in rules for hk in rule.get("hooks", [])]
            for ev, rules in settings.get("hooks", {}).items()}


def test_install_writes_every_shipped_hook_with_data_dir_baked_in(tmp_path):
    settings = tmp_path / "settings.json"
    report = H.install(settings_path=settings, repo_root=REPO, data_dir=tmp_path / "data")
    s = json.loads(settings.read_text())
    ev = _events(s)
    # the idle-inbox drain rides Stop; pane-state rides the lifecycle events; self-trigger rides PostToolUse
    assert any("agent-queue-drain.py" in c for c in ev["Stop"])
    assert any("state-event-hook.py" in c for c in ev["Stop"])
    assert any("state-event-hook.py" in c for c in ev["UserPromptSubmit"])
    assert any("state-event-hook.py" in c for c in ev["PreToolUse"])
    assert any("state-event-hook.py" in c for c in ev["SessionStart"])
    assert any("rotation-self-trigger.js" in c for c in ev["PostToolUse"])
    assert any("bus_feeder.py" in c for c in ev["Stop"])
    for c in ev["Stop"]:
        if "agent-queue-drain" in c:
            assert f'ORCHESTRA_DIR="{tmp_path / "data"}"' in c and str(REPO / "hooks") in c
    assert report["installed"] >= 5 and report["removed"] == 0
    # marker so a later install can find our own rows and nothing else
    assert all(H.MARKER in c for evs in ev.values() for c in evs)


def test_install_merges_and_never_clobbers_user_hooks(tmp_path):
    settings = tmp_path / "settings.json"
    user = {"model": "opus", "hooks": {"Stop": [{"matcher": "", "hooks": [
        {"type": "command", "command": "node /home/me/my-own-stop-hook.js"}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "bash /home/me/guard.sh"}]}]}}
    settings.write_text(json.dumps(user))
    H.install(settings_path=settings, repo_root=REPO, data_dir=tmp_path / "data")
    s = json.loads(settings.read_text())
    assert s["model"] == "opus"
    ev = _events(s)
    assert "node /home/me/my-own-stop-hook.js" in ev["Stop"]
    assert "bash /home/me/guard.sh" in ev["PreToolUse"]
    assert any("agent-queue-drain.py" in c for c in ev["Stop"])


def test_install_is_idempotent_and_replaces_only_its_own_rows(tmp_path):
    settings = tmp_path / "settings.json"
    H.install(settings_path=settings, repo_root=REPO, data_dir=tmp_path / "d1")
    r2 = H.install(settings_path=settings, repo_root=REPO, data_dir=tmp_path / "d2")
    s = json.loads(settings.read_text())
    ev = _events(s)
    drains = [c for c in ev["Stop"] if "agent-queue-drain" in c]
    assert len(drains) == 1 and "d2" in drains[0] and "d1" not in drains[0]
    assert r2["removed"] >= 5   # the d1 rows were replaced, not duplicated


def test_install_survives_missing_or_broken_settings_file(tmp_path):
    settings = tmp_path / "nested" / "settings.json"
    H.install(settings_path=settings, repo_root=REPO, data_dir=tmp_path / "data")
    assert settings.exists()
    settings.write_text("{not json")
    rep = H.install(settings_path=settings, repo_root=REPO, data_dir=tmp_path / "data")
    assert rep.get("error")            # refuses to clobber a file it cannot parse
    assert settings.read_text() == "{not json"


def test_status_reports_installed_vs_missing(tmp_path):
    settings = tmp_path / "settings.json"
    st = H.status(settings_path=settings, repo_root=REPO)
    assert st["installed"] == [] and len(st["missing"]) >= 5
    H.install(settings_path=settings, repo_root=REPO, data_dir=tmp_path / "data")
    st = H.status(settings_path=settings, repo_root=REPO)
    assert st["missing"] == []


def test_install_refuses_when_a_hook_script_is_missing(tmp_path):
    """A row pointing at a non-existent script errors on every tool call and blocks the host."""
    fake_root = tmp_path / "repo"; (fake_root / "hooks").mkdir(parents=True)
    settings = tmp_path / "settings.json"
    rep = H.install(settings_path=settings, repo_root=fake_root, data_dir=tmp_path / "data")
    assert rep.get("error") and "missing" in rep["error"]
    assert not settings.exists()


def test_install_refuses_the_real_settings_file_under_pytest(tmp_path):
    rep = H.install(settings_path=Path("~/.claude/settings.json"), repo_root=REPO, data_dir=tmp_path / "data")
    assert rep.get("error") and "inside a test" in rep["error"]


def test_installed_command_fails_open_when_the_script_disappears_later(tmp_path):
    """Runtime fail-open: a checkout that moves or a deleted script must never block a tool call.
    The install-time guard covers only install time; the installed command itself must exit 0
    when its script is gone (2026-09-17: a missing script blocked every tool on the host)."""
    import shutil, subprocess
    fake_root = tmp_path / "repo"
    shutil.copytree(REPO / "hooks", fake_root / "hooks", ignore=shutil.ignore_patterns("tests", "__pycache__"))
    (fake_root / "scripts" / "lineage_daemon").mkdir(parents=True)
    (fake_root / "scripts" / "lineage_daemon" / "bus_feeder.py").write_text("import sys; sys.exit(0)\n")
    settings = tmp_path / "settings.json"
    H.install(settings_path=settings, repo_root=fake_root, data_dir=tmp_path / "data")
    cmds = [h["command"] for rules in json.loads(settings.read_text())["hooks"].values() for r in rules for h in r["hooks"]]
    shutil.rmtree(fake_root)            # the checkout is gone
    for cmd in cmds:
        r = subprocess.run(["bash", "-c", cmd], input="{}", capture_output=True, text=True, timeout=20)
        assert r.returncode == 0, (cmd, r.returncode, r.stderr[-200:])


def test_installed_command_still_runs_the_script_when_present(tmp_path):
    """Fail-open must not mean fail-silent: with the script present its output (e.g. the drain's
    block decision) reaches Claude unchanged."""
    import subprocess
    fake_root = tmp_path / "repo"; (fake_root / "hooks").mkdir(parents=True)
    for _ev, _m, rel, _r in H.HOOKS:
        p = fake_root / rel; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('print("{\\"decision\\": \\"block\\", \\"reason\\": \\"probe\\"}")\n' if rel.endswith(".py") else "console.log('js-ok')\n")
    settings = tmp_path / "settings.json"
    H.install(settings_path=settings, repo_root=fake_root, data_dir=tmp_path / "data")
    cmds = [h["command"] for rules in json.loads(settings.read_text())["hooks"].values() for r in rules for h in r["hooks"]]
    drain = next(c for c in cmds if "agent-queue-drain" in c)
    r = subprocess.run(["bash", "-c", drain], input="{}", capture_output=True, text=True, timeout=20)
    assert r.returncode == 0 and '"block"' in r.stdout
