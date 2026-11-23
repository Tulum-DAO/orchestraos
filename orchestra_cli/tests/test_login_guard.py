"""Provider logins survive every upgrade (operator rule). Each guarantee has a test that fails without it:
the guard in init / upgrade / install-unit (sabotage: a step deletes, empties, swaps or loosens a FAKE login
file), the doctor rows, the seat environment, the systemd templates, the static no-writer scan, and the
defects the audit fixed. Every test is fenced: HOME, CLAUDE_CONFIG_DIR, CODEX_HOME, GROK_HOME, GEMINI_CLI_HOME
and ORCHESTRA_DIR point at tmp dirs holding FAKE login files; TMUX / TMUX_PANE are unset."""
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from orchestra_cli import __main__ as M
from orchestra_cli import doctor as D
from orchestra_cli import init_cmd as I
from orchestra_cli import install_unit as IU
from orchestra_cli import login_guard as LG
from orchestra_cli import settings as S
from orchestra_cli import upgrade_cmd as U

REPO = Path(__file__).resolve().parents[2]
FAKE_TOKEN = "FAKE-TOKEN-not-a-real-credential-0000"


@pytest.fixture
def fence(tmp_path, monkeypatch):
    """Every login path the guard resolves lands under tmp_path; assert it before anything runs."""
    home = tmp_path / "home"
    for name, sub in (("HOME", ""), ("CLAUDE_CONFIG_DIR", ".claude-fenced"), ("CODEX_HOME", ".codex-fenced"),
                      ("GROK_HOME", ".grok-fenced"), ("GEMINI_CLI_HOME", "gem-home")):
        d = home / sub if sub else home
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(d))
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    monkeypatch.setenv("ORCHESTRA_SKIP_HOOKS", "1")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    for k in LG.LOGIN_ENV:
        assert os.environ[k].startswith(str(tmp_path)), f"fence: {k} escaped tmp"
    for lf in LG.login_files():
        assert str(lf.path).startswith(str(tmp_path)), f"fence: {lf.path} escaped tmp"
    assert "TMUX" not in os.environ and "TMUX_PANE" not in os.environ
    return {"home": home, "data": data}


def _fake_logins(fence):
    """One FAKE login per provider, shaped like the real files (keys only; values are fake)."""
    claude_dir = Path(os.environ["CLAUDE_CONFIG_DIR"])
    cred = claude_dir / ".credentials.json"
    cred.write_text(json.dumps({"claudeAiOauth": {"accessToken": FAKE_TOKEN, "refreshToken": FAKE_TOKEN + "r"}}))
    cred.chmod(0o600)
    cj = claude_dir / ".claude.json"
    cj.write_text(json.dumps({"oauthAccount": {"accountUuid": "0000-fake-uuid", "emailAddress": "fake@example.invalid"},
                              "projects": {}}))
    codex = Path(os.environ["CODEX_HOME"]) / "auth.json"
    codex.write_text(json.dumps({"tokens": {"access_token": FAKE_TOKEN, "account_id": "acct_fake"}}))
    codex.chmod(0o600)
    gem = Path(os.environ["GEMINI_CLI_HOME"]) / ".gemini"
    gem.mkdir(parents=True, exist_ok=True)
    (gem / "oauth_creds.json").write_text(json.dumps({"refresh_token": FAKE_TOKEN}))
    grok = Path(os.environ["GROK_HOME"]) / "auth.json"
    grok.write_text(json.dumps({"token": FAKE_TOKEN}))
    return {"cred": cred, "claude_json": cj, "codex": codex, "gemini": gem / "oauth_creds.json", "grok": grok}


def _discard(p: Path):
    """No rm: the fake file is moved aside (the test's own tmp), which is what a deletion looks like."""
    p.rename(p.with_name(p.name + ".gone"))


# ---- the guard itself ---------------------------------------------------------------------------------

def test_login_files_follow_the_env_the_clis_read(fence):
    paths = {(lf.provider, lf.path.name): lf.path for lf in LG.login_files()}
    assert paths[("claude", ".credentials.json")].parent == Path(os.environ["CLAUDE_CONFIG_DIR"])
    assert paths[("claude", ".claude.json")].parent == Path(os.environ["CLAUDE_CONFIG_DIR"])
    assert paths[("codex", "auth.json")].parent == Path(os.environ["CODEX_HOME"])
    assert paths[("grok", "auth.json")].parent == Path(os.environ["GROK_HOME"])
    assert paths[("gemini", "oauth_creds.json")].parent == Path(os.environ["GEMINI_CLI_HOME"]) / ".gemini"
    # without the overrides everything hangs off HOME
    bare = {(lf.provider, lf.path.name): lf.path for lf in LG.login_files(env={"HOME": "/h"})}
    assert bare[("claude", ".credentials.json")] == Path("/h/.claude/.credentials.json")
    assert bare[("claude", ".claude.json")] == Path("/h/.claude.json")
    assert bare[("codex", "auth.json")] == Path("/h/.codex/auth.json")
    assert bare[("grok", "auth.json")] == Path("/h/.grok/auth.json")


def test_fingerprint_never_keeps_content_and_reads_the_login_shape(fence):
    f = _fake_logins(fence)
    fp = LG.fingerprint(next(lf for lf in LG.login_files() if lf.path == f["cred"]))
    assert fp["present"] and fp["login"] is True and fp["mode"] == 0o600 and len(fp["sha256"]) == 64
    assert FAKE_TOKEN not in json.dumps(fp)
    cj = LG.fingerprint(next(lf for lf in LG.login_files() if lf.path == f["claude_json"]))
    assert cj["login"] is True and cj["identity"] and "0000-fake-uuid" not in json.dumps(cj)


@pytest.mark.parametrize("sabotage,word", [
    ("vanish", "VANISHED"),
    ("empty", "no longer holds a login"),
    ("swap", "DIFFERENT account"),
    ("loosen", "mode opened"),
])
def test_compare_names_every_way_a_login_can_be_lost(fence, sabotage, word):
    f = _fake_logins(fence)
    before = LG.snapshot()
    if sabotage == "vanish":
        _discard(f["cred"])
    elif sabotage == "empty":
        f["codex"].write_text("{}")
    elif sabotage == "swap":
        f["claude_json"].write_text(json.dumps({"oauthAccount": {"accountUuid": "1111-other", "emailAddress": "o@x"}}))
    elif sabotage == "loosen":
        f["cred"].chmod(0o644)
    fatal = [x for x in LG.compare(before, LG.snapshot(), "npm:root") if x.fatal]
    assert len(fatal) == 1 and word in fatal[0].reason and "npm:root" in fatal[0].line()
    assert fatal[0].path in {str(p) for p in f.values()}
    assert FAKE_TOKEN not in fatal[0].line()


def test_a_token_refresh_by_the_cli_is_a_note_not_a_failure(fence):
    f = _fake_logins(fence)
    before = LG.snapshot()
    f["cred"].write_text(json.dumps({"claudeAiOauth": {"accessToken": FAKE_TOKEN + "-rotated"}}))
    f["claude_json"].write_text(json.dumps({"oauthAccount": {"accountUuid": "0000-fake-uuid", "emailAddress": "fake@example.invalid"},
                                            "projects": {"/x": {"hasTrustDialogAccepted": True}}}))
    found = LG.compare(before, LG.snapshot(), "hooks")
    assert found and not any(x.fatal for x in found)
    assert all("intact" in x.reason for x in found)


def test_stamp_is_0600_and_holds_hashes_only(fence):
    _fake_logins(fence)
    g = LG.LoginGuard(stamp_path=LG.stamp_path_for(fence["data"])).begin()
    assert g.after("nothing") == []
    out = g.seal("test")
    assert out and out.exists() and stat.S_IMODE(out.stat().st_mode) == 0o600
    text = out.read_text()
    assert FAKE_TOKEN not in text and "fake@example" not in text and "0000-fake-uuid" not in text
    doc = json.loads(text)
    assert set(doc) == {"stamp", "command", "files", "baked"} and doc["command"] == "test"
    assert all(set(v) <= {"provider", "present", "kind", "size", "mode", "sha256", "login", "identity"} for v in doc["files"].values())


# ---- guarantee 1: `orchestra init` ----------------------------------------------------------------------

def _repo(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "orchestra.example.toml").write_text(
        '[data]\ndir = "/var/lib/orchestraos"\n[gateway]\nhost = "127.0.0.1"\nport = 8890\n'
        '[dashboard]\nhost = "127.0.0.1"\nport = 8891\n[notify]\nchannel = "none"\n[runtimes]\nenabled = ["claude"]\n')
    (root / "requirements.txt").write_text("aiohttp\n")
    for sub in ("api", "dashboard"):
        (root / sub).mkdir()
        (root / sub / "package.json").write_text("{}")
    (root / "package.json").write_text("{}")
    return root


class Runner:
    """A fake subprocess runner that can sabotage a login file when a given command runs."""

    def __init__(self, sabotage=None, on=("npm", "install")):
        self.sabotage, self.on, self.calls = sabotage, tuple(on), []

    def __call__(self, argv, cwd=None, env=None):
        argv = list(argv)
        self.calls.append(tuple(argv))
        cwd = Path(cwd) if cwd else Path(".")
        if "venv" in argv:
            (Path(argv[-1]) / "bin").mkdir(parents=True, exist_ok=True)
            (Path(argv[-1]) / "bin" / "python").write_text("")
        elif argv[:2] == ["npm", "install"]:
            (cwd / "node_modules").mkdir(exist_ok=True)
        elif argv[:3] == ["npm", "run", "build"]:
            (cwd / "dist").mkdir(exist_ok=True)
            (cwd / "dist" / ("server.js" if cwd.name == "api" else "index.html")).write_text("")
        if self.sabotage and tuple(argv[:len(self.on)]) == self.on:
            self.sabotage()
            self.sabotage = None
        return 0


def test_init_passes_and_stamps_when_logins_are_untouched(fence, tmp_path):
    _fake_logins(fence)
    rep = I.run_init(_repo(tmp_path), fence["data"], run=Runner(), yes=True)
    rows = [r for r in rep if r.step == I.LOGINS_STEP]
    assert len(rows) == 1 and not rows[0].did and "preserved" in rows[0].detail and "claude" in rows[0].detail
    assert not I.failed_steps(rep)
    assert LG.stamp_path_for(fence["data"]).exists()


def test_init_fails_loudly_naming_file_and_step_when_a_step_deletes_a_login(fence, tmp_path, capsys):
    f = _fake_logins(fence)
    rep = I.run_init(_repo(tmp_path), fence["data"], run=Runner(sabotage=lambda: _discard(f["cred"])), yes=True)
    failed = I.failed_steps(rep)
    assert [r.step for r in failed] == [I.LOGINS_STEP]
    assert str(f["cred"]) in failed[0].detail and "VANISHED" in failed[0].detail
    assert "npm:root" in failed[0].detail                       # the step the deletion was noticed in
    assert "LOGIN LOST" in capsys.readouterr().err
    assert not f["cred"].exists(), "the guard must never write a login file back"
    assert not LG.stamp_path_for(fence["data"]).exists(), "no stamp after a harmed login"
    # the init exit code follows: cmd_init returns 1 on any failed step
    assert len([r for r in rep if r.step == I.LOGINS_STEP]) == 1


def test_init_fails_when_a_step_swaps_the_codex_account(fence, tmp_path):
    f = _fake_logins(fence)

    def swap():
        f["codex"].write_text(json.dumps({"tokens": {"access_token": "x", "account_id": "acct_OTHER"}}))
    rep = I.run_init(_repo(tmp_path), fence["data"], run=Runner(sabotage=swap, on=("npm", "run", "build")), yes=True)
    failed = I.failed_steps(rep)
    assert len(failed) == 1 and "DIFFERENT account" in failed[0].detail and str(f["codex"]) in failed[0].detail
    assert "build:" in failed[0].detail


def test_init_with_no_login_files_reports_none_and_does_not_fail(fence, tmp_path):
    rep = I.run_init(_repo(tmp_path), fence["data"], run=Runner(), yes=True)
    row = next(r for r in rep if r.step == I.LOGINS_STEP)
    assert not row.did and "0 provider login file(s)" in row.detail and not I.failed_steps(rep)


def test_init_tolerates_a_token_refresh_during_a_step(fence, tmp_path):
    f = _fake_logins(fence)

    def refresh():
        f["cred"].write_text(json.dumps({"claudeAiOauth": {"accessToken": "rotated"}}))
    rep = I.run_init(_repo(tmp_path), fence["data"], run=Runner(sabotage=refresh), yes=True)
    assert not I.failed_steps(rep)
    assert "rewritten by their own CLI" in next(r for r in rep if r.step == I.LOGINS_STEP).detail


# ---- guarantee 1: `orchestra upgrade` --------------------------------------------------------------------

class Git:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, cwd=None):
        self.calls.append(tuple(argv))
        if argv[:2] == ["git", "log"]:
            return 0, "abc1234 fix: x"
        return 0, ""


def test_upgrade_stops_with_exit_3_when_init_harms_a_login(fence, tmp_path, capsys):
    f = _fake_logins(fence)
    doctors = []

    def init(root, **kw):
        _discard(f["codex"])
        return []
    rep = U.run_upgrade(tmp_path, git=Git(), init=init, doctor=lambda r: doctors.append(r) or 0)
    assert not rep.ok and rep.exit_code == U.LOGIN_EXIT == 3
    assert str(f["codex"]) in rep.error and "`init`" in rep.error and "VANISHED" in rep.error
    assert doctors == [], "the upgrade stops at the harmed phase"
    assert "LOGIN LOST" in capsys.readouterr().out
    assert "upgrade stopped" in rep.render() and "did not write any login file" in rep.error
    assert not f["codex"].exists()


def test_upgrade_stops_when_the_pull_phase_harms_a_login(fence, tmp_path):
    f = _fake_logins(fence)
    inits = []

    class PullGit(Git):
        def __call__(self, argv, cwd=None):
            if argv[:2] == ["git", "pull"]:
                f["cred"].write_text("")             # emptied, e.g. by a hook
            return super().__call__(argv, cwd)
    rep = U.run_upgrade(tmp_path, git=PullGit(), init=lambda r, **k: inits.append(1), doctor=lambda r: 0)
    assert rep.exit_code == 3 and "`pull`" in rep.error and inits == []


def test_upgrade_passes_and_reports_intact_logins(fence, tmp_path, monkeypatch):
    _fake_logins(fence)
    rep = U.run_upgrade(tmp_path, git=Git(), init=lambda r, **k: [], doctor=lambda r: 0)
    assert rep.ok and rep.exit_code == 0 and "intact" in rep.logins and "logins:" in rep.render()


# ---- guarantee 1: `orchestra install-unit` --------------------------------------------------------------

def _unit_ns(tmp_path, **kw):
    ns = M.parse_args(["install-unit", "--dest", str(tmp_path / "dest"), "--no-sudo", "--user",
                       __import__("pwd").getpwuid(os.getuid()).pw_name, "--python", sys.executable])
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _install_unit_settings(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    (root / "deploy").mkdir(exist_ok=True)
    (root / "deploy" / "systemd").symlink_to(REPO / "deploy" / "systemd")
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "providers.json").symlink_to(REPO / "config" / "providers.json")
    cfg = root / "orchestra.toml"
    cfg.write_text(f'[data]\ndir = "{tmp_path / "data"}"\n[runtimes]\nenabled = ["claude"]\n')
    monkeypatch.setenv("ORCHESTRA_ROOT", str(root))
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(cfg))


def test_install_unit_returns_3_when_the_install_harms_a_login(fence, tmp_path, monkeypatch, capsys):
    f = _fake_logins(fence)
    _install_unit_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(IU, "_npm_global_bin", lambda *a, **k: None)

    def sabotaged_execute(plan, **kw):
        f["grok"].write_text("not json")
        return 0
    monkeypatch.setattr(IU, "execute", sabotaged_execute)
    assert IU.cmd_install_unit(_unit_ns(tmp_path)) == IU.LOGIN_EXIT == 3
    err = capsys.readouterr().err
    assert "LOGIN LOST" in err and str(f["grok"]) in err and "install-unit" in err


def test_install_unit_passes_and_stamps_when_logins_are_untouched(fence, tmp_path, monkeypatch):
    _fake_logins(fence)
    _install_unit_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(IU, "_npm_global_bin", lambda *a, **k: None)
    monkeypatch.setattr(IU, "execute", lambda plan, **kw: 0)
    assert IU.cmd_install_unit(_unit_ns(tmp_path)) == 0
    assert LG.stamp_path_for(tmp_path / "data").exists()


def _kw(tmp_path):
    return dict(user=__import__("pwd").getpwuid(os.getuid()).pw_name, allow_root=True, dest=tmp_path / "dest",
                path_env="/usr/bin:/bin", home="/home/someone", npm_bin="", python="/usr/bin/python3",
                which=lambda c, path=None: f"/x/{c}")


def _effective_unit_env(plan, unit):
    """What `systemctl show -p Environment <unit>` would list: the unit's Environment= plus its drop-in's."""
    return LG.parse_unit_env(plan.rendered[unit], plan.dropins.get(unit, ""))


def _simulated_recovery_seat_env(unit_env, home):
    """What a seat resumed by orchestra-recovery.service sees: systemd gives HOME from User=, the unit's
    effective Environment, and the pane keeps whatever spawn-agent.sh's prefix carries (the same variables)."""
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    env.update(unit_env)
    return env


# ---- D1 (gm ruling): the login dirs the operator signed in under are BAKED into the seat-starting units ------

def test_install_unit_bakes_only_a_set_var_whose_dir_holds_a_login(fence, tmp_path, monkeypatch):
    """Condition 1: set + login found -> baked; set + no login -> warned, not baked; unset -> nothing baked."""
    _fake_logins(fence)
    _install_unit_settings(tmp_path, monkeypatch)
    st = S.load_settings(repo_root=tmp_path / "repo", config_path=tmp_path / "repo" / "orchestra.toml")
    env = {"HOME": os.environ["HOME"], "CLAUDE_CONFIG_DIR": os.environ["CLAUDE_CONFIG_DIR"], "CODEX_HOME": os.environ["CODEX_HOME"],
           "GROK_HOME": str(tmp_path / "empty-grok")}            # set, but nothing signed in there
    plan = IU.build_plan(st, ["stack"], env=env, **_kw(tmp_path))
    assert plan.login_env == {"CLAUDE_CONFIG_DIR": env["CLAUDE_CONFIG_DIR"], "CODEX_HOME": env["CODEX_HOME"]}
    assert set(plan.dropins) == {"orchestra.service", "orchestra-recovery.service"}
    for unit in plan.dropins:
        eff = _effective_unit_env(plan, unit)
        assert eff["CLAUDE_CONFIG_DIR"] == env["CLAUDE_CONFIG_DIR"] and eff["CODEX_HOME"] == env["CODEX_HOME"]
        assert "GROK_HOME" not in eff and "GEMINI_CLI_HOME" not in eff
        assert not re.search(r'Environment="HOME=', plan.dropins[unit]), "HOME comes from User=, never baked"
    assert any("WARNING: GROK_HOME=" in n and "NOT baked" in n for n in plan.notes)
    assert not any("CLAUDE_CONFIG_DIR" in n and "WARNING" in n for n in plan.notes)
    # the rendered units themselves still carry no login dir (the drop-in does); unset = empty drop-in
    assert not any("CLAUDE_CONFIG_DIR" in t for t in plan.rendered.values())
    bare = IU.build_plan(st, ["stack"], env={"HOME": "/home/someone"}, **_kw(tmp_path))
    assert bare.login_env == {} and bare.dropins and not any("Environment=" in t for t in bare.dropins.values())
    assert not any("WARNING" in n for n in bare.notes)


def test_install_unit_drop_in_carries_paths_only_is_0600_and_a_rerun_replaces_it(fence, tmp_path, monkeypatch):
    """Conditions 2 and 4: a second install under another value UPDATES the drop-in (one install -D -m 0600 per
    seat-starting unit, every run); the drop-in holds paths, never a token or a copy of a config file."""
    f = _fake_logins(fence)
    _install_unit_settings(tmp_path, monkeypatch)
    st = S.load_settings(repo_root=tmp_path / "repo", config_path=tmp_path / "repo" / "orchestra.toml")
    env1 = {"HOME": os.environ["HOME"], "CLAUDE_CONFIG_DIR": os.environ["CLAUDE_CONFIG_DIR"]}
    plan1 = IU.build_plan(st, ["stack"], env=env1, **_kw(tmp_path))
    ran = []
    assert IU.execute(plan1, euid=1000, interactive=False, runner=lambda c: ran.append(c) or 0,
                      sudo_usable=lambda i: True, out=lambda s: None) == 0
    installs = [c for c in ran if c[:4] == ["sudo", "install", "-D", "-m"]]
    assert [c[4] for c in installs] == ["0600", "0600"]
    assert {Path(c[-1]).name for c in installs} == {LG.DROPIN_NAME}
    staged = plan1.staging / plan1.dropin_rel("orchestra.service")
    assert stat.S_IMODE(staged.stat().st_mode) == 0o600
    text = staged.read_text()
    assert FAKE_TOKEN not in text and "claudeAiOauth" not in text and f["cred"].read_text()[:10] not in text
    assert text.count("Environment=") == 1 and f'CLAUDE_CONFIG_DIR={env1["CLAUDE_CONFIG_DIR"]}' in text
    # re-run under a different dir that also holds a login: the staged drop-in is replaced, not kept
    other = tmp_path / "home" / ".claude-other"
    other.mkdir()
    (other / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "FAKE2"}}))
    plan2 = IU.build_plan(st, ["stack"], env={"HOME": os.environ["HOME"], "CLAUDE_CONFIG_DIR": str(other)}, **_kw(tmp_path))
    assert IU.execute(plan2, euid=1000, interactive=False, runner=lambda c: 0, sudo_usable=lambda i: True, out=lambda s: None) == 0
    text2 = staged.read_text()
    assert f"CLAUDE_CONFIG_DIR={other}" in text2 and env1["CLAUDE_CONFIG_DIR"] not in text2
    # re-run with the variable unset: the drop-in is written again, now carrying nothing (default dirs)
    plan3 = IU.build_plan(st, ["stack"], env={"HOME": os.environ["HOME"]}, **_kw(tmp_path))
    assert IU.execute(plan3, euid=1000, interactive=False, runner=lambda c: 0, sudo_usable=lambda i: True, out=lambda s: None) == 0
    assert "Environment=" not in staged.read_text()


def test_cmd_install_unit_records_the_bake_in_the_stamp_and_doctor_names_dir_and_baked(fence, tmp_path, monkeypatch, capsys):
    """Condition 2 (doctor): logins:<provider> names the resolved dir and whether it is baked or default."""
    _fake_logins(fence)
    _install_unit_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(IU, "_npm_global_bin", lambda *a, **k: None)
    monkeypatch.setattr(IU, "execute", lambda plan, **kw: 0)
    assert IU.cmd_install_unit(_unit_ns(tmp_path)) == 0
    stamp = json.loads(LG.stamp_path_for(tmp_path / "data").read_text())
    assert stamp["baked"] == {"CLAUDE_CONFIG_DIR": os.environ["CLAUDE_CONFIG_DIR"], "CODEX_HOME": os.environ["CODEX_HOME"],
                              "GROK_HOME": os.environ["GROK_HOME"], "GEMINI_CLI_HOME": os.environ["GEMINI_CLI_HOME"]}
    assert FAKE_TOKEN not in json.dumps(stamp)
    rows = dict((r[0], r) for r in LG.doctor_rows(tmp_path / "data"))
    assert rows["logins:claude"][1] == "OK" and f"dir {os.environ['CLAUDE_CONFIG_DIR']}, baked into the units" in rows["logins:claude"][2]
    # the same install seen from a shell without the variable: the row says which dir the units were baked with
    plain = {"HOME": os.environ["HOME"]}
    rows = dict((r[0], r) for r in LG.doctor_rows(tmp_path / "data", env=plain, home=os.environ["HOME"]))
    assert "units baked with " + os.environ["CLAUDE_CONFIG_DIR"] in rows["logins:claude"][2]
    # and a stamp with nothing baked says default
    LG.write_stamp(LG.stamp_path_for(tmp_path / "data"), "init", LG.snapshot(plain, os.environ["HOME"]), {})
    rows = dict((r[0], r) for r in LG.doctor_rows(tmp_path / "data", env=plain, home=os.environ["HOME"]))
    assert "default" in rows["logins:claude"][2]


def test_a_baked_dir_that_lost_its_login_fails_init_upgrade_and_install_unit_loudly(fence, tmp_path, monkeypatch, capsys):
    """Condition 3: never a silent fall-through to the default dir."""
    f = _fake_logins(fence)
    data = fence["data"]
    LG.write_stamp(LG.stamp_path_for(data), "install-unit", LG.snapshot(), {"CODEX_HOME": os.environ["CODEX_HOME"]})
    # healthy: init and upgrade pass
    root = _repo(tmp_path)
    assert not I.failed_steps(I.run_init(root, data, run=Runner(), yes=True))
    _discard(f["codex"])                                   # the baked dir has no login any more
    rep = I.run_init(root, data, run=Runner(), yes=True)
    failed = I.failed_steps(rep)
    assert failed and "baked into the systemd units as CODEX_HOME" in failed[0].detail and os.environ["CODEX_HOME"] in failed[0].detail
    assert "LOGIN LOST" in capsys.readouterr().err
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(tmp_path / "repo" / "orchestra.toml"))
    (tmp_path / "repo" / "orchestra.toml").write_text(f'[data]\ndir = "{data}"\n')
    git = Git()
    up = U.run_upgrade(tmp_path / "repo", git=git, init=lambda r, **k: [], doctor=lambda r: 0)
    assert up.exit_code == 3 and "CODEX_HOME" in up.error and "Nothing was pulled" in up.error
    assert not any(c[:2] == ("git", "fetch") for c in git.calls)
    # install-unit from a shell that still names the dead dir: exit 3; from a shell pointing at a dir with a login: proceeds
    (tmp_path / "repo" / "orchestra.toml").write_text(f'[data]\ndir = "{data}"\n[runtimes]\nenabled = ["claude"]\n')
    monkeypatch.setattr(IU, "_npm_global_bin", lambda *a, **k: None)
    monkeypatch.setenv("ORCHESTRA_ROOT", str(tmp_path / "repo"))
    for sub in ("deploy", "config"):
        (tmp_path / "repo" / sub).mkdir(exist_ok=True)
    (tmp_path / "repo" / "deploy" / "systemd").symlink_to(REPO / "deploy" / "systemd")
    (tmp_path / "repo" / "config" / "providers.json").symlink_to(REPO / "config" / "providers.json")
    ran = []
    monkeypatch.setattr(IU, "execute", lambda plan, **kw: ran.append(plan) or 0)
    assert IU.cmd_install_unit(_unit_ns(tmp_path)) == 3 and ran == []
    assert "CODEX_HOME" in capsys.readouterr().err
    good = tmp_path / "home" / ".codex-good"
    good.mkdir()
    (good / "auth.json").write_text(json.dumps({"tokens": {"access_token": "FAKE3", "account_id": "acct_fake"}}))
    monkeypatch.setenv("CODEX_HOME", str(good))
    assert IU.cmd_install_unit(_unit_ns(tmp_path)) == 0 and ran[0].login_env["CODEX_HOME"] == str(good)
    assert json.loads(LG.stamp_path_for(data).read_text())["baked"]["CODEX_HOME"] == str(good)
    # --forget-login-dirs is the explicit way out when the dir is simply gone
    monkeypatch.delenv("CODEX_HOME")
    LG.write_stamp(LG.stamp_path_for(data), "install-unit", LG.snapshot(), {"CODEX_HOME": str(tmp_path / "nowhere")})
    assert IU.cmd_install_unit(_unit_ns(tmp_path)) == 3
    assert IU.cmd_install_unit(_unit_ns(tmp_path, forget_login_dirs=True)) == 0
    assert "CODEX_HOME" not in json.loads(LG.stamp_path_for(data).read_text())["baked"]


def test_a_recovered_seat_under_the_baked_units_reads_the_operators_login(fence, tmp_path, monkeypatch):
    """Condition 5, by effect on the rendered files: install under a custom CLAUDE_CONFIG_DIR + CODEX_HOME,
    read back the units' effective Environment (unit + drop-in, as `systemctl show -p Environment` would), run a
    simulated recovery (the seat's env = HOME from User= + that Environment + spawn-agent.sh's prefix), and the
    seat's login fingerprint equals the pre-install one. Sabotage: without the drop-in the seat reads ~/.claude."""
    f = _fake_logins(fence)
    _install_unit_settings(tmp_path, monkeypatch)
    st = S.load_settings(repo_root=tmp_path / "repo", config_path=tmp_path / "repo" / "orchestra.toml")
    shell = {"HOME": os.environ["HOME"], "CLAUDE_CONFIG_DIR": os.environ["CLAUDE_CONFIG_DIR"], "CODEX_HOME": os.environ["CODEX_HOME"]}
    before = {p: fp for p, fp in LG.snapshot(shell, shell["HOME"]).items() if fp["present"]}
    plan = IU.build_plan(st, ["stack"], env=shell, **_kw(tmp_path))
    assert IU.execute(plan, euid=1000, interactive=False, runner=lambda c: 0, sudo_usable=lambda i: True, out=lambda s: None) == 0
    unit_env = _effective_unit_env(plan, "orchestra-recovery.service")
    assert unit_env["CLAUDE_CONFIG_DIR"] == shell["CLAUDE_CONFIG_DIR"] and unit_env["CODEX_HOME"] == shell["CODEX_HOME"]
    seat_env = _simulated_recovery_seat_env(unit_env, shell["HOME"])
    # spawn-agent.sh's prefix carries the same variables into the pane (the real lines, under bash)
    src = (REPO / "spawn-agent.sh").read_text()
    block = src[src.index("    _orch_prefix=\"$(printf 'ORCHESTRA_DIR=%q"):src.index('    launch_cmd="$_orch_prefix $launch_cmd"')]
    script = tmp_path / "prefix_recovery_lg.sh"
    script.write_text("ORCHESTRA_DIR=/d SCRIPT_DIR=/s agent_id=gm\n" + block + "\nprintf '%s\\n' \"$_orch_prefix\"\n")
    prefix = subprocess.run(["bash", str(script)], env=seat_env, capture_output=True, text=True, check=True).stdout
    pane_env = dict(seat_env, **dict(kv.split("=", 1) for kv in prefix.split() if kv.startswith(("CLAUDE_CONFIG_DIR=", "CODEX_HOME="))))
    after = {p: fp for p, fp in LG.snapshot(pane_env, pane_env["HOME"]).items() if fp["present"]}
    assert after == before, "the recovered seat sees exactly the login files the operator's shell had"
    assert not [x for x in LG.compare(before, LG.snapshot(pane_env, pane_env["HOME"])) if x.fatal]
    # SABOTAGE: remove the bake (a unit without the drop-in): the seat resolves the DEFAULT dirs, where nothing is signed in
    unit_env_sabotaged = LG.parse_unit_env(plan.rendered["orchestra-recovery.service"])
    seat_env_sabotaged = _simulated_recovery_seat_env(unit_env_sabotaged, shell["HOME"])
    prefix = subprocess.run(["bash", str(script)], env=seat_env_sabotaged, capture_output=True, text=True, check=True).stdout
    assert "CLAUDE_CONFIG_DIR=" not in prefix
    lost = LG.snapshot(seat_env_sabotaged, shell["HOME"])
    assert not any(fp["present"] for fp in lost.values()), "without the bake the seat reads ~/.claude and ~/.codex: signed out"
    assert [x for x in LG.compare(before, lost) if x.fatal], "the guard calls that a lost login"
    assert LG.provider_dirs(seat_env_sabotaged, shell["HOME"])["claude"] == (str(Path(shell["HOME"]) / ".claude"), "default")


# ---- guarantee 2: doctor rows --------------------------------------------------------------------------

def test_doctor_rows_intact_changed_and_absent(fence):
    f = _fake_logins(fence)
    data = fence["data"]
    rows = dict((r[0], r) for r in LG.doctor_rows(data))
    assert rows["logins:claude"][1] == "INFO" and "not stamped" in rows["logins:claude"][2]
    LG.LoginGuard(stamp_path=LG.stamp_path_for(data)).begin().seal("upgrade")
    rows = dict((r[0], r) for r in LG.doctor_rows(data))
    assert rows["logins:claude"][1] == "OK" and rows["logins:claude"][2].startswith("intact since ")
    assert rows["logins:codex"][1] == "OK" and rows["logins:gemini"][1] == "OK" and rows["logins:grok"][1] == "OK"
    # a token refresh keeps it intact; a vanished file is CHANGED, naming the file
    f["cred"].write_text(json.dumps({"claudeAiOauth": {"accessToken": "rotated"}}))
    _discard(f["codex"])
    rows = dict((r[0], r) for r in LG.doctor_rows(data))
    assert rows["logins:claude"][1] == "OK"
    assert rows["logins:codex"][1] == "INFO" and "no codex login found" in rows["logins:codex"][2]
    f["grok"].write_text("{}")
    rows = dict((r[0], r) for r in LG.doctor_rows(data))
    assert rows["logins:grok"][1] == "WARN" and "CHANGED since" in rows["logins:grok"][2] and str(f["grok"]) in rows["logins:grok"][2]
    assert FAKE_TOKEN not in json.dumps(rows)


def test_doctor_row_on_macos_names_the_keychain(fence):
    rows = dict((r[0], r) for r in LG.doctor_rows(fence["data"], platform="darwin"))
    assert rows["logins:claude"][1] == "INFO" and "Keychain" in rows["logins:claude"][2]


def test_run_doctor_carries_the_logins_rows_and_they_never_gate(fence, tmp_path):
    from orchestra_cli.tests.test_doctor import _probes, _repo as doctor_repo
    _fake_logins(fence)
    (tmp_path / "doc").mkdir()
    root = doctor_repo(tmp_path / "doc")
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    checks = {c.name: c for c in D.run_doctor(st, _probes())}
    assert checks["logins:claude"].status == D.INFO and not checks["logins:claude"].required
    LG.LoginGuard(stamp_path=LG.stamp_path_for(st.data_dir)).begin().seal("upgrade")
    checks = {c.name: c for c in D.run_doctor(st, _probes())}
    assert checks["logins:claude"].status == D.OK and "intact since" in checks["logins:claude"].detail
    assert "logins:claude" in D.render_table(list(checks.values()))


# ---- guarantee 3: the seat environment ---------------------------------------------------------------

def _st(tmp_path):
    root = tmp_path / "repo2"
    root.mkdir(exist_ok=True)
    cfg = root / "orchestra.toml"
    cfg.write_text(f'[data]\ndir = "{tmp_path / "data"}"\n[runtimes]\nenabled = ["claude", "codex", "gemini", "grok"]\n')
    return S.load_settings(repo_root=root, config_path=cfg)


def test_child_env_keeps_every_login_dir_variable_exactly(fence, tmp_path):
    base = {"PATH": "/usr/bin:/bin", "HOME": "/home/op", "CLAUDE_CONFIG_DIR": "/home/op/.claude-work",
            "CODEX_HOME": "/home/op/.codex-w", "GROK_HOME": "/home/op/.grok-w", "GEMINI_CLI_HOME": "/home/op/g",
            "GEMINI_API_KEY": "fake-key", "GEMINI_MODEL": "x"}  # pragma: allowlist secret
    env = S.child_env(_st(tmp_path), base=base)
    for k, v in base.items():
        if k != "PATH":
            assert env[k] == v, k
    bare = S.child_env(_st(tmp_path), base={"PATH": "/usr/bin", "HOME": "/home/op"})
    assert bare["HOME"] == "/home/op"
    assert not any(k in bare for k in LG.LOGIN_ENV if k != "HOME"), "a seat never gets a login dir the operator did not set"
    assert not [k for k in bare if k.startswith("GEMINI_")]


def test_the_installer_session_scrub_lists_never_name_a_login_variable():
    src = (REPO / "spawn-agent.sh").read_text()
    arr = next(l for l in src.splitlines() if l.startswith("INSTALLER_SESSION_ENV=("))
    names = set(arr[len("INSTALLER_SESSION_ENV=("):].rstrip(")").split()) | set(S.INSTALLER_SESSION_ENV)
    assert not names & set(LG.LOGIN_ENV)
    assert not {n for n in names if n.startswith("GEMINI_") and n != "GEMINI_CLI"}


def test_spawn_agent_carries_every_login_dir_into_the_pane(fence, tmp_path):
    """By effect: the real prefix-building lines, run under bash with the login dirs set."""
    src = (REPO / "spawn-agent.sh").read_text()
    start = src.index("    _orch_prefix=\"$(printf 'ORCHESTRA_DIR=%q")
    end = src.index('    launch_cmd="$_orch_prefix $launch_cmd"', start)
    block = src[start:end]
    script = tmp_path / "prefix_lg.sh"
    script.write_text("ORCHESTRA_DIR=/d SCRIPT_DIR=/s agent_id=seat\n" + block + "\nprintf '%s\\n' \"$_orch_prefix\"\n")
    env = {"PATH": "/usr/bin:/bin", "CLAUDE_CONFIG_DIR": "/op/.claude-w", "CODEX_HOME": "/op/.codex-w",
           "GROK_HOME": "/op/.grok-w", "GEMINI_CLI_HOME": "/op/g"}
    out = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, check=True).stdout
    for k, v in env.items():
        if k != "PATH":
            assert f"{k}={v}" in out, out
    assert "HOME=" not in out.replace("CODEX_HOME=", "").replace("GROK_HOME=", "").replace("GEMINI_CLI_HOME=", ""), \
        "HOME is the tmux server's (same user) and is never overridden"
    plain = subprocess.run(["bash", str(script)], env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=True).stdout
    assert not any(k in plain for k in LG.LOGIN_ENV)


def test_systemd_templates_derive_home_from_the_user_and_bake_no_login_dir(fence, tmp_path, monkeypatch):
    tdir = REPO / "deploy" / "systemd"
    for t in sorted(tdir.glob("*.service.template")):
        text = t.read_text()
        assert "User={{ORCHESTRA_USER}}" in text, t.name
        assert not re.search(r'Environment="?HOME=', text), f"{t.name}: HOME comes from User=, never baked"
        for k in LG.LOGIN_ENV:
            if k != "HOME":
                assert k not in text, f"{t.name} carries {k}"
    _install_unit_settings(tmp_path, monkeypatch)
    st = S.load_settings(repo_root=tmp_path / "repo", config_path=tmp_path / "repo" / "orchestra.toml")
    plan = IU.build_plan(st, ["stack", "telemetryd"], user=__import__("pwd").getpwuid(os.getuid()).pw_name,
                         allow_root=True, force=True, dest=tmp_path / "dest", path_env="/usr/bin:/bin", home="/home/someone",
                         npm_bin="", python="/usr/bin/python3", which=lambda c, path=None: f"/x/{c}",
                         env={"CLAUDE_CONFIG_DIR": "/leak"})
    for unit, text in plan.rendered.items():
        assert "/leak" not in text and "HOME=" not in text, unit


# ---- guarantee 4: nothing in the tree writes a provider login path ---------------------------------------

SCAN_DIRS = ("orchestra_cli", "scripts", "hooks", "deploy", "services")
LOGIN_PATH_RE = re.compile(
    r"\.credentials\.json|\.claude\.json|oauth_creds|antigravity-oauth-token"
    r"|(?:\.codex|CODEX_HOME|codex_home)[^\n]{0,60}auth\.json"
    r"|(?:\.grok|GROK_HOME|grok_home)[^\n]{0,60}(?:auth|credentials)\.json")
PY_WRITE_RE = re.compile(
    r"\b(?:write_text|write_bytes|os\.remove|os\.unlink|\.unlink\(|os\.rename|os\.replace|shutil\.(?:copy\w*|move|rmtree)"
    r"|os\.chmod|\.chmod\(|json\.dump\(|NamedTemporaryFile|os\.O_WRONLY|os\.O_CREAT|os\.O_TRUNC|open\([^)]*['\"][wax]\b)")
SH_WRITE_RE = re.compile(
    r"(?:^|[;&|(]\s*|\$\(\s*)\s*(?:sudo\s+)?(?:rm|mv|cp|chmod|chown|truncate|shred|tee|rsync|install|ln)\s"
    r"|(?<![0-9])>>?\s*\"?\$?[A-Za-z~/{](?!dev/null)")
WINDOW = 4
# A file may appear here ONLY with a reason; the test asserts every entry still exists and still hits.
ALLOWLIST = {
    # Merge-only, flock + atomic replace, sets projects[<cwd>].hasTrustDialogAccepted so an interactive seat
    # does not freeze at the trust dialog. oauthAccount and every other key survive (test below). Not a
    # copy, not a move, not a delete; the file is Claude's own state file, re-read by Claude each start.
    "scripts/ensure_cwd_trusted.py",
}


def _login_writers():
    found = {}
    for d in SCAN_DIRS:
        for dp, dn, fn in os.walk(REPO / d):
            dn[:] = [x for x in dn if x not in ("node_modules", "tests", "__pycache__", ".venv", "fixtures")]
            for f in fn:
                if f.startswith("test_") or f.endswith("_test.py") or not f.endswith((".py", ".sh", ".template", ".js", ".ts", ".mjs")):
                    continue
                p = Path(dp) / f
                rel = str(p.relative_to(REPO))
                try:
                    lines = p.read_text(errors="replace").splitlines()
                except OSError:
                    continue
                wr = PY_WRITE_RE if f.endswith((".py", ".js", ".ts", ".mjs")) else SH_WRITE_RE
                for i, line in enumerate(lines):
                    if not LOGIN_PATH_RE.search(line):
                        continue
                    near = [j + 1 for j in range(max(0, i - WINDOW), min(len(lines), i + WINDOW + 1)) if wr.search(lines[j])]
                    if near:
                        found.setdefault(rel, []).append(f"{rel}:{i + 1} (write verb at {near})")
    return found


def test_no_file_in_the_tree_writes_deletes_moves_or_chmods_a_login_path():
    found = _login_writers()
    unexplained = {k: v for k, v in found.items() if k not in ALLOWLIST}
    assert not unexplained, "a provider login path next to a write/delete/move/chmod:\n" + "\n".join(
        x for v in unexplained.values() for x in v)
    stale = [a for a in ALLOWLIST if a not in found]
    assert not stale, f"allowlist entries that no longer hit (drop them): {stale}"


def test_the_scan_catches_a_planted_writer(tmp_path, monkeypatch):
    """The scanner is not passing on absent evidence: a planted file is found."""
    fake_repo = tmp_path / "r"
    (fake_repo / "scripts").mkdir(parents=True)
    (fake_repo / "scripts" / "bad.py").write_text('p = os.path.expanduser("~/.claude/.credentials.json")\nos.remove(p)\n')
    (fake_repo / "scripts" / "bad.sh").write_text('rm -f "$CODEX_HOME/auth.json"\n')
    monkeypatch.setattr(sys.modules[__name__], "REPO", fake_repo)
    found = _login_writers()
    assert set(found) == {"scripts/bad.py", "scripts/bad.sh"}


def test_ensure_cwd_trusted_keeps_the_account_record(fence, tmp_path):
    """The one allowlisted writer: it adds a trust bit and leaves oauthAccount (and everything else) intact."""
    f = _fake_logins(fence)
    before = json.loads(f["claude_json"].read_text())
    cwd = tmp_path / "proj"
    cwd.mkdir()
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "ensure_cwd_trusted.py"), str(cwd)],
                       env=dict(os.environ), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    after = json.loads(f["claude_json"].read_text())
    assert after["oauthAccount"] == before["oauthAccount"]
    assert after["projects"][str(cwd)]["hasTrustDialogAccepted"] is True
    assert {k: v for k, v in after.items() if k != "projects"} == {k: v for k, v in before.items() if k != "projects"}
    snap_b = {str(f["claude_json"]): LG.fingerprint(next(lf for lf in LG.login_files() if lf.path == f["claude_json"]))}
    assert not [x for x in LG.compare(snap_b, LG.snapshot()) if x.fatal]


# ---- guarantee 5: the defects the audit found ------------------------------------------------------------

def test_agent_recovery_looks_for_claude_transcripts_under_the_login_dir():
    """Defect: agent-recovery.sh read $HOME/.claude/projects, so on a CLAUDE_CONFIG_DIR install it never
    found a transcript (and could not tell a held sid). It follows the login dir now."""
    src = (REPO / "scripts" / "agent-recovery.sh").read_text()
    assert '"$HOME"/.claude/projects' not in src and "$HOME/.claude/projects" not in src
    assert len(re.findall(r"\$\{CLAUDE_CONFIG_DIR:-\$HOME/\.claude\}\"?/projects", src)) == 2


def test_install_client_logged_in_check_honours_codex_home():
    src = (REPO / "scripts" / "install-client.sh").read_text()
    assert 'expanduser("~/.codex/auth.json")' not in src
    assert 'os.environ.get("CODEX_HOME")' in src


def test_nothing_in_the_tree_runs_a_provider_logout_or_touches_a_keychain():
    bad = re.compile(r"\b(?:claude|codex|gemini|agy|grok)\s+(?:auth\s+)?logout\b|security\s+(?:delete|add)-(?:generic|internet)-password", re.I)
    hits = []
    for d in SCAN_DIRS + ("spawn-agent.sh", "Makefile", "Dockerfile"):
        p = REPO / d
        files = [p] if p.is_file() else [q for q in p.rglob("*") if q.is_file() and "node_modules" not in q.parts and "tests" not in q.parts]
        for q in files:
            if q.suffix not in (".py", ".sh", ".template", ".js", ".ts", ".mjs", "") or q.name.startswith("test_"):
                continue
            try:
                for i, line in enumerate(q.read_text(errors="replace").splitlines()):
                    if bad.search(line) and not line.lstrip().startswith("#"):
                        hits.append(f"{q.relative_to(REPO)}:{i + 1}")
            except OSError:
                pass
    assert not hits, hits


# ---- a copied (or shared) login is a logout: no container path copies or bind-mounts a host login -------------

LOGIN_DIR_RE = re.compile(r"(?:\$\{?(?:HOME|localEnv:HOME)\}?|~)/\.(?:claude|codex|gemini|grok)\b")


def test_the_devcontainer_gives_the_container_its_own_login_never_the_hosts():
    """Host and container Claude refreshing ONE OAuth token (a bind mount of ~/.claude, or a copy) logs the
    second one out. The dev container mounts a named volume: a separate identity, signed in once inside."""
    doc = json.loads((REPO / ".devcontainer" / "devcontainer.json").read_text())
    for m in doc.get("mounts", []):
        kv = dict(part.split("=", 1) for part in m.split(",") if "=" in part)
        if kv.get("target", "").endswith((".claude", ".codex", ".gemini", ".grok")):
            assert kv.get("type") == "volume", f"a login dir is bind-mounted from the host: {m}"
        assert not LOGIN_DIR_RE.search(kv.get("source", "")), f"host login dir mounted: {m}"
    assert any(m.startswith("source=orchestraos-claude,") and "type=volume" in m for m in doc["mounts"])


def test_no_dockerfile_or_doc_copies_or_bind_mounts_a_host_login():
    docker_copy = re.compile(r"^\s*(?:COPY|ADD)\b.*\.(?:claude|codex|gemini|grok)\b|docker\s+cp\b.*\.(?:claude|codex|gemini|grok)\b")
    bind = re.compile(r"-v\s+~/\.(?:claude|codex|gemini|grok)[:/]|--mount[^\n]*source=(?:\$\{?HOME\}?|~)/\.(?:claude|codex|gemini|grok)")
    hits = []
    files = [REPO / "Dockerfile", REPO / "Makefile"] + sorted((REPO / "docs").glob("*.md")) + sorted((REPO / "scripts").glob("*.sh")) \
        + sorted((REPO / ".devcontainer").glob("*"))
    for f in files:
        if not f.is_file():
            continue
        for i, line in enumerate(f.read_text(errors="replace").splitlines()):
            if docker_copy.search(line) or bind.search(line):
                hits.append(f"{f.relative_to(REPO)}:{i + 1}: {line.strip()[:80]}")
    assert not hits, "a host login copied or bind-mounted into a container (a copied login is a logout):\n" + "\n".join(hits)
