"""A scratch checkout never installs into the operator's real Claude settings (2026-09-20 / 2026-10-09).

`orchestra init --yes`, run from a scratchpad clone and outside pytest, wrote 12 `#orchestraos-hook` rows into
an operator's live ~/.claude/settings.json. They pointed into the scratch dir and sat there for 19 days: every
tool call of every session forked a shell for them, and anyone re-creating that path would have had their code
run by every session. The pytest-only guard could not see it. Here: (1) install() refuses a checkout or data
dir under a temp dir when the target is the real file; (2) `--remove` takes every tagged row out, nothing else;
(3) the root conftest fails any test run that changes the real file's tagged rows.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "hooks"))
import install as H  # noqa: E402


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A fake HOME whose ~/.claude/settings.json is what install() treats as the REAL file. The pytest guard
    is lifted (as outside pytest, where the incident happened) so only the new guard stands in the way."""
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv(H.ALLOW_TEMP_ENV, raising=False)
    # The test's whole tmp_path is under /tmp; only its scratchpad/ plays the temp dir here.
    scratch = (tmp_path / "scratchpad").resolve()
    scratch.mkdir(exist_ok=True)
    monkeypatch.setattr(H, "_temp_roots", lambda: [scratch])
    assert H._real_settings() == (h / ".claude" / "settings.json").resolve()
    return h


def _outside_pytest(monkeypatch):
    """pytest sets PYTEST_CURRENT_TEST again for each test phase, so this is lifted in the test body."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)


def _scratch_clone(tmp_path):
    clone = tmp_path / "scratchpad" / "red2"
    for _ev, _m, rel, _r in H.HOOKS:
        (clone / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, clone / rel)
    return clone


def test_a_scratch_checkout_is_refused_for_the_real_settings_file(home, tmp_path, monkeypatch):
    _outside_pytest(monkeypatch)
    real = home / ".claude" / "settings.json"
    real.write_text(json.dumps({"model": "keep-me"}))
    before = real.read_bytes()
    rep = H.install(settings_path=real, repo_root=_scratch_clone(tmp_path), data_dir=tmp_path / "red2-data")
    assert rep["installed"] == 0 and "under a temp dir" in rep["error"] and "CLAUDE_CONFIG_DIR" in rep["error"]
    assert real.read_bytes() == before                                   # not one byte written
    plan = H.plan(settings_path=real, repo_root=_scratch_clone(tmp_path), data_dir=tmp_path / "d")
    assert "under a temp dir" in H.render_plan(plan)                      # `orchestra init` shows the refusal


def test_a_real_checkout_with_a_scratch_data_dir_is_refused_too(home, tmp_path, monkeypatch):
    _outside_pytest(monkeypatch)
    if H._under_temp(REPO):
        pytest.skip("this checkout itself lives under a temp dir")
    rep = H.install(settings_path=home / ".claude" / "settings.json", repo_root=REPO,
                    data_dir=tmp_path / "scratchpad" / "data")
    assert rep["installed"] == 0 and "data dir under a temp dir" in rep["error"]


def test_the_same_scratch_install_into_a_sandboxed_config_dir_still_works(home, tmp_path):
    sandbox = tmp_path / "scratchpad" / "claude-config" / "settings.json"
    rep = H.install(settings_path=sandbox, repo_root=_scratch_clone(tmp_path), data_dir=tmp_path / "red2-data")
    assert not rep.get("error") and rep["installed"] == len(H.HOOKS)
    assert not (home / ".claude" / "settings.json").exists()


def test_the_scratch_install_goes_through_only_when_asked_for_by_name(home, tmp_path, monkeypatch):
    _outside_pytest(monkeypatch)
    monkeypatch.setenv(H.ALLOW_TEMP_ENV, "1")
    rep = H.install(settings_path=home / ".claude" / "settings.json", repo_root=_scratch_clone(tmp_path),
                    data_dir=tmp_path / "red2-data")
    assert not rep.get("error") and rep["installed"] == len(H.HOOKS)


def test_any_settings_file_that_is_not_scratch_is_guarded_not_only_home(home, tmp_path, monkeypatch):
    # #323 review SF2: an operator's file outside $HOME/.claude (--settings, a non-default CLAUDE_CONFIG_DIR)
    _outside_pytest(monkeypatch)
    elsewhere = tmp_path / "operator-config" / "settings.json"
    rep = H.install(settings_path=elsewhere, repo_root=_scratch_clone(tmp_path), data_dir=tmp_path / "d")
    assert rep["installed"] == 0 and "under a temp dir" in rep["error"] and not elsewhere.exists()


def test_a_temp_dir_that_holds_home_refuses_nothing_there(home, tmp_path, monkeypatch):
    # TMPDIR=$HOME: the checkout and the settings file are both "temp", so nothing is scratch-into-real.
    _outside_pytest(monkeypatch)
    monkeypatch.setattr(H, "_temp_roots", lambda: [tmp_path.resolve()])
    rep = H.install(settings_path=home / ".claude" / "settings.json", repo_root=_scratch_clone(tmp_path),
                    data_dir=tmp_path / "d")
    assert not rep.get("error") and rep["installed"] == len(H.HOOKS)


def test_remove_keeps_empty_user_rules_text_and_a_symlinked_file(tmp_path):
    # #323 review SF3 + SF4
    target = tmp_path / "dotfiles" / "settings.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"note": "café", "hooks": {
        "PreToolUse": [{"matcher": "keep-empty", "hooks": []},
                       {"hooks": [{"type": "command", "command": "x #orchestraos-hook"}]}],
        "PostToolUse": [], "Odd": {"not": "a list"}, "Stop": ["not a rule", {"hooks": ["not an entry"]}]}},
        ensure_ascii=False))
    link = tmp_path / "settings.json"
    link.symlink_to(target)
    rep = H.remove(settings_path=link)
    assert rep["removed"] == 1 and not rep.get("error")
    assert link.is_symlink()                                              # still a link
    raw = target.read_text(encoding="utf-8")
    assert "café" in raw and "#orchestraos-hook" not in raw
    out = json.loads(raw)
    assert out["hooks"] == {"PreToolUse": [{"matcher": "keep-empty", "hooks": []}], "PostToolUse": [],
                            "Odd": {"not": "a list"}, "Stop": ["not a rule", {"hooks": ["not an entry"]}]}


def test_remove_takes_out_every_tagged_row_and_nothing_else(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"model": "m", "permissions": {"allow": ["Bash(ls)"]}, "hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "node /home/me/my-stop.js"}]}]}}))
    H.install(settings_path=s, repo_root=REPO, data_dir=tmp_path / "a")
    # a second, stray install from elsewhere: its rows carry the same tag
    data = json.loads(s.read_text())
    data["hooks"]["PreToolUse"][0]["hooks"].append(
        {"type": "command", "command": '[ -f "/tmp/x/red2/hooks/h.py" ] && python3 h.py || exit 0 #orchestraos-hook'})
    s.write_text(json.dumps(data))
    rep = H.remove(settings_path=s)
    assert rep["removed"] == len(H.HOOKS) + 1 and not rep.get("error")
    out = json.loads(s.read_text())
    assert out["model"] == "m" and out["permissions"] == {"allow": ["Bash(ls)"]}
    assert out["hooks"] == {"Stop": [{"hooks": [{"type": "command", "command": "node /home/me/my-stop.js"}]}]}
    assert H.remove(settings_path=s) == {"removed": 0, "settings": str(s)}   # idempotent, file untouched
    assert H.main(["--settings", str(s), "--remove"]) == 0


def test_remove_leaves_a_broken_file_alone(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text("{not json")
    assert "not valid JSON" in H.remove(settings_path=s)["error"] and s.read_text() == "{not json"


# ---- the belt: a test run that changes the real file's tagged rows fails, whatever its tests said --------
_PROBE = '''
import json, os
def test_writes_the_real_file():
    p = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    data = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true #orchestraos-hook"}]}]}}
    if %s:
        with open(p, "w") as f:
            json.dump(data, f)
'''


_BREAKS = '''
import os
def test_breaks_the_real_file():
    with open(os.path.join(os.path.expanduser("~"), ".claude", "settings.json"), "w") as f:
        f.write("{not json")
'''


@pytest.mark.parametrize("writes", [True, False, "breaks"])
def test_a_run_that_changes_the_real_settings_file_fails(tmp_path, writes):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    if writes == "breaks":     # #323 review SF1: an unreadable file is reported, never a crash in the hook
        (h / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "true #orchestraos-hook"}]}]}}))
    probe = tmp_path / "probe" / "test_probe.py"
    probe.parent.mkdir()
    probe.write_text(_BREAKS if writes == "breaks" else _PROBE % writes)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "PYTEST_CURRENT_TEST")}
    import site
    env["HOME"] = str(h)
    env["PYTHONUSERBASE"] = site.getuserbase()                 # pytest may live in the real user site
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "conftest", "-p", "no:cacheprovider",
                        str(probe)], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    if writes == "breaks":
        assert r.returncode == 1 and "changed the REAL" in out and "Traceback" not in out, out[-1500:]
    elif writes:
        assert r.returncode == 1 and "changed the REAL" in out and "1 added" in out, out[-1500:]
    else:
        assert r.returncode == 0 and "changed the REAL" not in out, out[-1500:]
