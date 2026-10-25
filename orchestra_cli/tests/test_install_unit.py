"""`orchestra install-unit` (issue G3): rendering, the PATH it bakes, the telemetryd double-run
guard, and that every install path is driven by fakes. No test here runs sudo or systemctl or
writes outside tmp_path."""
import configparser
import os
import pwd

import pytest

from orchestra_cli import __main__ as M
from orchestra_cli import install_unit as IU
from orchestra_cli import settings as S

REPO = S.repo_root_from_env()
ME = pwd.getpwuid(os.getuid()).pw_name


def _settings(tmp_path, telemetry=True, runtimes=("claude",)):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    # templates + providers come from the real checkout, config from tmp
    (root / "deploy").mkdir(exist_ok=True)
    if not (root / "deploy" / "systemd").exists():
        (root / "deploy" / "systemd").symlink_to(REPO / "deploy" / "systemd")
    (root / "config").mkdir(exist_ok=True)
    if not (root / "config" / "providers.json").exists():
        (root / "config" / "providers.json").symlink_to(REPO / "config" / "providers.json")
    cfg = root / "orchestra.toml"
    rt = ", ".join(f'"{r}"' for r in runtimes)
    cfg.write_text(f'[data]\ndir = "{tmp_path / "data"}"\n[telemetry]\nenabled = {"true" if telemetry else "false"}\n'
                   f'[runtimes]\nenabled = [{rt}]\n')
    return S.load_settings(repo_root=root, config_path=cfg)


def _plan(tmp_path, sets=None, telemetry=True, runtimes=("claude",), **kw):
    st = _settings(tmp_path, telemetry=telemetry, runtimes=runtimes)
    kw.setdefault("user", ME)
    kw.setdefault("allow_root", True)
    kw.setdefault("dest", tmp_path / "dest")
    kw.setdefault("path_env", "/usr/bin:/bin")
    kw.setdefault("home", "/home/someone")
    kw.setdefault("npm_bin", "")
    kw.setdefault("python", "/usr/bin/python3")
    kw.setdefault("which", lambda c, path=None: f"/x/{c}")
    return IU.build_plan(st, sets or ["stack"], **kw)


def _cfg(text):
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string(text)
    return cp


def _env(text):
    out = {}
    for line in text.splitlines():
        if line.startswith("Environment="):
            k, v = line[len("Environment="):].strip('"').split("=", 1)
            out[k] = v
    return out


def test_parse_install_unit():
    ns = M.parse_args(["install-unit", "--unit", "telemetryd", "--dry-run", "--print", "--no-sudo"])
    assert ns.command == "install-unit" and ns.unit == ["telemetryd"] and ns.dry_run and ns.print and ns.no_sudo
    ns = M.parse_args(["install-unit"])
    assert ns.unit is None and ns.dest == "/etc/systemd/system" and not ns.now


def test_every_template_renders_with_no_placeholder_left(tmp_path):
    plan = _plan(tmp_path, ["stack", "telemetryd"], telemetry=False)
    assert set(plan.rendered) == {"orchestra.service", "orchestra-recovery.service",
                                  "orchestra-reconciler.service", "orchestra-reconciler.timer",
                                  "orchestra-telemetryd.service"}
    for unit, text in plan.rendered.items():
        assert "{{" not in text, unit
        if unit.endswith(".timer"):
            continue
        svc = _cfg(text)["Service"]
        assert svc["User"] == ME
        assert svc["WorkingDirectory"] == str((tmp_path / "repo").resolve())
        assert _env(text)["ORCHESTRA_DIR"] == str(tmp_path / "data")


def test_stack_unit_runs_up_in_the_foreground(tmp_path):
    svc = _cfg(_plan(tmp_path).rendered["orchestra.service"])["Service"]
    assert svc["ExecStart"] == '"/usr/bin/python3" -m orchestra_cli up'
    assert "--detach" not in svc["ExecStart"] and "-d" not in svc["ExecStart"].split()
    assert svc["Type"] == "simple" and svc["Restart"] == "on-failure"
    assert svc["KillMode"] == "process"          # restarting the stack never kills the seats
    assert "NoNewPrivileges" not in svc          # seats inherit the sandbox; sudo must work in them


def test_recovery_unit_is_a_oneshot_after_the_stack(tmp_path):
    cp = _cfg(_plan(tmp_path).rendered["orchestra-recovery.service"])
    assert "orchestra.service" in cp["Unit"]["After"].split()
    svc = cp["Service"]
    assert svc["Type"] == "oneshot" and svc["RemainAfterExit"] == "yes" and svc["KillMode"] == "process"
    assert svc["ExecStart"].endswith('/scripts/agent-recovery.sh" --boot')


def test_npm_prefix_usr_does_not_reorder_the_system_dirs():
    p = IU.build_path("", "/home/u", npm_bin="/usr/bin", exists=lambda d: True)
    assert p.split(":") == ["/home/u/.local/bin", "/home/u/.bun/bin", "/home/u/.grok/bin", "/usr/local/bin", "/usr/bin", "/bin"]


def test_system_dirs_always_come_last():
    p = IU.build_path("/usr/bin:/snap/bin:/bin:/home/u/.local/bin", "/home/u", exists=lambda d: False)
    assert p.split(":") == ["/snap/bin", "/home/u/.local/bin", "/usr/local/bin", "/usr/bin", "/bin"]


def test_path_always_has_local_bin_then_system_dirs():
    p = IU.build_path("/opt/a:relative:/opt/a:", "/home/u", npm_bin=None, exists=lambda d: False)
    parts = p.split(":")
    assert parts == ["/opt/a", "/home/u/.local/bin", "/usr/local/bin", "/usr/bin", "/bin"]


def test_path_adds_bun_and_npm_bins_only_when_they_exist():
    have = {"/home/u/.bun/bin", "/opt/npm/bin"}
    p = IU.build_path("/usr/bin", "/home/u", npm_bin="/opt/npm/bin", exists=lambda d: d in have).split(":")
    assert p == ["/home/u/.local/bin", "/home/u/.bun/bin", "/opt/npm/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    p = IU.build_path("/usr/bin", "/home/u", npm_bin="/opt/npm/bin", exists=lambda d: False).split(":")
    assert "/home/u/.bun/bin" not in p and "/opt/npm/bin" not in p


def test_substituted_units_carry_path_with_local_bin(tmp_path):
    plan = _plan(tmp_path, ["stack", "telemetryd"], telemetry=False, home="/home/someone")
    for unit, text in plan.rendered.items():
        if unit.endswith(".timer"):
            continue
        path = _env(text)["PATH"].split(":")
        assert "/home/someone/.local/bin" in path, unit
        assert path[-3:] == ["/usr/local/bin", "/usr/bin", "/bin"], unit


def test_warns_when_a_configured_cli_is_not_on_the_unit_path(tmp_path):
    seen = []

    def which(cmd, path=None):
        seen.append((cmd, path))
        return "/home/someone/.local/bin/claude" if cmd == "claude" else None

    plan = _plan(tmp_path, runtimes=("claude", "gemini", "codex"), which=which)
    warned = " ".join(plan.notes)
    assert "`agy` (gemini" in warned and "`codex` (codex" in warned and "`claude`" not in warned
    assert all(path == plan.values["PATH"] for _, path in seen)   # resolved on the UNIT's PATH


def test_no_warning_when_every_cli_resolves(tmp_path):
    assert _plan(tmp_path, runtimes=("claude", "codex")).notes == []


def test_refuses_root_and_unknown_users(tmp_path):
    with pytest.raises(IU.UnitError, match="root"):
        _plan(tmp_path, user="root", allow_root=False)
    with pytest.raises(IU.UnitError, match="no such user"):
        _plan(tmp_path, user="no-such-user-g3-xyz")


def test_render_escapes_percent_and_rejects_quotes():
    vals = {k: "/x" for k in IU.PLACEHOLDERS}
    vals["ORCHESTRA_ROOT"] = "/srv/100%"
    assert IU.render("WorkingDirectory={{ORCHESTRA_ROOT}}\n", vals) == "WorkingDirectory=/srv/100%%\n"
    vals["ORCHESTRA_ROOT"] = '/srv/"q"'
    with pytest.raises(IU.UnitError):
        IU.render("X={{ORCHESTRA_ROOT}}", vals)
    with pytest.raises(IU.UnitError, match="unknown placeholder"):
        IU.render("X={{NOPE}}", {k: "/x" for k in IU.PLACEHOLDERS})


def test_telemetryd_twice_is_refused_unless_forced_or_disabled(tmp_path):
    with pytest.raises(IU.UnitError, match="telemetry"):
        _plan(tmp_path, ["stack", "telemetryd"], telemetry=True)
    # an already-installed stack unit counts too
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "orchestra.service").write_text("[Unit]\n")
    with pytest.raises(IU.UnitError, match="telemetry"):
        _plan(tmp_path, ["telemetryd"], telemetry=True, dest=dest)
    forced = _plan(tmp_path, ["telemetryd"], telemetry=True, dest=dest, force=True)
    assert any("WARNING (--force)" in n for n in forced.notes)
    assert _plan(tmp_path, ["telemetryd"], telemetry=False, dest=dest).units == ["orchestra-telemetryd.service"]


def test_dry_run_writes_nothing_and_runs_nothing(tmp_path):
    plan = _plan(tmp_path)
    ran, out = [], []
    rc = IU.execute(plan, dry_run=True, euid=1000, runner=ran.append, out=out.append)
    assert rc == 0 and ran == [] and not plan.staging.exists()
    assert any("systemctl enable orchestra.service orchestra-recovery.service" in l for l in out)


def test_installs_with_sudo_when_dest_is_not_writable(tmp_path):
    plan = _plan(tmp_path, dest=tmp_path / "not-there")
    ran = []
    rc = IU.execute(plan, euid=1000, interactive=False, runner=lambda c: ran.append(c) or 0,
                    sudo_usable=lambda i: True, out=lambda s: None)
    assert rc == 0
    assert [c[:3] for c in ran] == [["sudo", "install", "-m"], ["sudo", "systemctl", "daemon-reload"],
                                     ["sudo", "systemctl", "enable"]]
    assert ran[2][3:] == ["orchestra.service", "orchestra-recovery.service", "orchestra-reconciler.timer"]
    assert (plan.staging / "orchestra.service").read_text() == plan.rendered["orchestra.service"]


def test_writable_dest_needs_no_sudo_and_now_starts(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    plan = _plan(tmp_path, dest=dest, now=True)
    ran = []
    IU.execute(plan, euid=1000, runner=lambda c: ran.append(c) or 0, sudo_usable=lambda i: False,
               out=lambda s: None)
    assert all(c[0] != "sudo" for c in ran)
    assert ran[-1][:3] == ["systemctl", "enable", "--now"]


def test_no_sudo_stages_and_prints_the_commands(tmp_path):
    plan = _plan(tmp_path, dest=tmp_path / "not-there")
    ran, out = [], []
    rc = IU.execute(plan, use_sudo=False, euid=1000, runner=ran.append, out=out.append)
    text = "\n".join(out)
    assert rc == 0 and ran == []
    assert "NOT installed" in text and "sudo systemctl daemon-reload" in text
    assert (plan.staging / "orchestra-recovery.service").exists()


def test_sudo_unavailable_is_a_failure_with_instructions(tmp_path):
    plan = _plan(tmp_path, dest=tmp_path / "not-there")
    out = []
    rc = IU.execute(plan, euid=1000, interactive=False, runner=lambda c: 0,
                    sudo_usable=lambda i: False, out=out.append)
    assert rc == 1 and any("sudo install" in l for l in out)


def test_a_failing_step_stops_the_install(tmp_path):
    plan = _plan(tmp_path, dest=tmp_path / "not-there")
    ran = []
    rc = IU.execute(plan, euid=1000, interactive=True, sudo_usable=lambda i: True,
                    runner=lambda c: ran.append(c) or (1 if "daemon-reload" in c else 0), out=lambda s: None)
    assert rc == 1 and len(ran) == 2


def test_cli_print_renders_without_writing(tmp_path, capsys, monkeypatch):
    st = _settings(tmp_path)
    monkeypatch.setenv("ORCHESTRA_ROOT", str(st.repo_root))
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(st.config_path))
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    rc = M.main(["install-unit", "--print", "--dest", str(tmp_path / "dest"), "--force"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "# ---- " + str(tmp_path / "dest" / "orchestra.service") in out
    assert "-m orchestra_cli up" in out and "{{" not in out
    assert not (tmp_path / "data" / "systemd").exists() and not (tmp_path / "dest").exists()


def test_cli_conflict_exits_2(tmp_path, capsys, monkeypatch):
    st = _settings(tmp_path, telemetry=True)
    monkeypatch.setenv("ORCHESTRA_ROOT", str(st.repo_root))
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(st.config_path))
    rc = M.main(["install-unit", "--unit", "stack", "--unit", "telemetryd", "--dry-run",
                 "--dest", str(tmp_path / "dest")])
    assert rc == 2 and "telemetry" in capsys.readouterr().err


def test_reconciler_runs_every_15_minutes_from_a_timer(tmp_path):
    plan = _plan(tmp_path)
    timer = _cfg(plan.rendered["orchestra-reconciler.timer"])
    assert timer["Timer"]["OnUnitActiveSec"] == "15min"
    assert timer["Timer"]["Unit"] == "orchestra-reconciler.service"
    assert timer["Install"]["WantedBy"] == "timers.target"
    svc = _cfg(plan.rendered["orchestra-reconciler.service"])["Service"]
    assert svc["Type"] == "oneshot"
    assert svc["ExecStart"].startswith('"/usr/bin/python3" -m scripts.identity_store.identity_reconciler --cron')
    assert f'--dir "{tmp_path / "data"}"' in svc["ExecStart"]
    assert _env(plan.rendered["orchestra-reconciler.service"])["ORCHESTRA_DIR"] == str(tmp_path / "data")


def test_timer_driven_service_is_installed_but_not_enabled(tmp_path):
    plan = _plan(tmp_path)
    assert "orchestra-reconciler.service" in plan.units
    assert "orchestra-reconciler.service" not in plan.enable_units
    assert "orchestra-reconciler.timer" in plan.enable_units
    enable = [c for c in plan.commands(sudo=False) if c[:2] == ["systemctl", "enable"]][0]
    assert "orchestra-reconciler.timer" in enable and "orchestra-reconciler.service" not in enable
