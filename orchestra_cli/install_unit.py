"""`orchestra install-unit`: render the systemd unit templates and install them (issue G3).

The templates live in deploy/systemd/*.service.template with {{PLACEHOLDER}} fields for
everything that differs per install: the user the services run as, the checkout, the data dir,
the config path, the python interpreter and the PATH. This module fills them in from Settings
(the same values `orchestra up` uses), refuses to leave a placeholder behind, and then either

  * writes the files itself when it can write the destination (root, or a --dest you own),
  * or runs `sudo install` + `sudo systemctl daemon-reload` + `sudo systemctl enable ...`,
  * or (--no-sudo, or sudo is not available) stages the rendered files under
    <data>/systemd/ and prints the exact commands to finish, so no one hand-edits a unit.

`--print` writes the rendered units to stdout; `--dry-run` reports every step and does nothing.

Unit sets:
  stack       orchestra.service (`orchestra up` in the foreground) + orchestra-recovery.service
              (agent-recovery.sh --boot, once, after it). The default: a box that survives reboot.
  telemetryd  orchestra-telemetryd.service, the daemon alone, for installs that do NOT run the
              stack under `orchestra up` (or run it with `[telemetry] enabled = false`).
`orchestra up` already supervises telemetryd, so installing both while telemetry is enabled
would run two daemons on one data dir; that is refused unless --force.
"""
from __future__ import annotations

import getpass
import os
import pwd
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import settings as S

DEFAULT_DEST = Path("/etc/systemd/system")
TEMPLATE_DIR_REL = Path("deploy") / "systemd"

# unit set -> unit file names, in install order
UNIT_SETS = {
    "stack": ("orchestra.service", "orchestra-recovery.service"),
    "telemetryd": ("orchestra-telemetryd.service",),
}
STACK_UNIT = "orchestra.service"
TELEMETRYD_UNIT = "orchestra-telemetryd.service"

PLACEHOLDERS = ("ORCHESTRA_USER", "ORCHESTRA_ROOT", "ORCHESTRA_DIR", "ORCHESTRA_CONFIG",
                "PYTHON", "PATH")
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_]+)\}\}")
# A value systemd would mis-parse inside Environment="K=V" / ExecStart="..." / WorkingDirectory.
_FORBIDDEN_CHARS = ('"', "\\", "\n", "\r")


class UnitError(ValueError):
    """A value or a combination install-unit will not write."""


def template_dir(repo_root: Path) -> Path:
    return Path(repo_root) / TEMPLATE_DIR_REL


def template_path(repo_root: Path, unit: str) -> Path:
    return template_dir(repo_root) / f"{unit}.template"


def _escape(name: str, value: str) -> str:
    value = str(value)
    if not value:
        raise UnitError(f"{name} is empty")
    bad = [c for c in _FORBIDDEN_CHARS if c in value]
    if bad:
        raise UnitError(f"{name} contains a character a unit file cannot carry ({bad!r}): {value!r}")
    # `%` starts a systemd specifier (%h, %u, ...) in every field these values land in.
    return value.replace("%", "%%")


def render(template: str, values: dict) -> str:
    """Fill every {{NAME}}; raise UnitError for an unknown or missing placeholder."""
    missing = [n for n in PLACEHOLDERS if n not in values]
    if missing:
        raise UnitError("missing values for " + ", ".join(missing))
    escaped = {k: _escape(k, v) for k, v in values.items()}

    def sub(m):
        name = m.group(1)
        if name not in escaped:
            raise UnitError(f"template has an unknown placeholder {{{{{name}}}}}")
        return escaped[name]

    out = _PLACEHOLDER_RE.sub(sub, template)
    left = _PLACEHOLDER_RE.findall(out)
    if left:  # a value that itself looked like a placeholder
        raise UnitError("placeholders left after rendering: " + ", ".join(sorted(set(left))))
    return out


def render_unit(repo_root: Path, unit: str, values: dict) -> str:
    return render(template_path(repo_root, unit).read_text(), values)


SYSTEM_PATH = ("/usr/local/bin", "/usr/bin", "/bin")
# Fallback when config/providers.json is unreadable: runtime id -> the CLI binary a seat runs.
DEFAULT_RUNTIME_CLIS = {"claude": "claude", "gemini": "agy", "codex": "codex", "grok": "grok"}


def _npm_global_bin(run=subprocess.run) -> Optional[str]:
    """`$(npm config get prefix)/bin`, where `npm install -g` puts agent CLIs; None without npm."""
    if not shutil.which("npm"):
        return None
    try:
        r = run(["npm", "config", "get", "prefix"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    prefix = (r.stdout or "").strip()
    return os.path.join(prefix, "bin") if r.returncode == 0 and os.path.isabs(prefix) else None


def build_path(raw: str, home: str, npm_bin: Optional[str] = None,
               exists: Callable[[str], bool] = os.path.isdir) -> str:
    """The PATH baked into every unit. systemd's own PATH is /usr/local/sbin:...:/bin, which finds
    no CLI installed per user (`claude` in ~/.local/bin on a stock install), so a seat started by
    the unit would fail to launch its agent. Order: the installing user's PATH (empty, relative and
    duplicate entries dropped, and the three system dirs held back for the end), then
    $HOME/.local/bin (always, even before it exists: the native Claude Code installer creates it
    later), ~/.bun/bin and the npm global bin when they exist, then /usr/local/bin:/usr/bin:/bin.
    A per-user CLI therefore wins over a stale system copy of the same name."""
    seen, out = set(), []

    def add(p):
        if p and os.path.isabs(p) and p not in seen:
            seen.add(p)
            out.append(p)

    for p in (raw or "").split(os.pathsep):
        if p not in SYSTEM_PATH:
            add(p)
    add(os.path.join(home, ".local", "bin"))
    bun = os.path.join(home, ".bun", "bin")
    if exists(bun):
        add(bun)
    if npm_bin and npm_bin not in SYSTEM_PATH and exists(npm_bin):   # prefix /usr -> /usr/bin: keep it last
        add(npm_bin)
    for p in SYSTEM_PATH:
        add(p)
    return os.pathsep.join(out)


def runtime_clis(st: S.Settings) -> dict:
    """runtime id -> CLI binary for each runtime in `[runtimes] enabled`, from config/providers.json."""
    import json
    mapping = dict(DEFAULT_RUNTIME_CLIS)
    try:
        data = json.loads((Path(st.repo_root) / "config" / "providers.json").read_text())
        provs = data.get("providers", []) if isinstance(data, dict) else data
        for p in provs:
            if isinstance(p, dict) and p.get("id") and p.get("cli"):
                mapping[str(p["id"])] = str(p["cli"])
    except (OSError, ValueError):
        pass
    return {r: mapping.get(r, r) for r in st.runtimes_enabled}


def unresolved_clis(clis: dict, path: str, which=shutil.which) -> list:
    """`[(runtime, cli)]` for every configured CLI the unit's PATH cannot find."""
    return [(r, c) for r, c in clis.items() if not which(c, path=path)]


def default_user(env: Optional[dict] = None, euid: Optional[int] = None) -> str:
    """The user to run the services as: $SUDO_USER under sudo, else the current user."""
    env = os.environ if env is None else env
    euid = os.geteuid() if euid is None else euid
    if euid == 0 and env.get("SUDO_USER") and env["SUDO_USER"] != "root":
        return env["SUDO_USER"]
    try:
        return pwd.getpwuid(euid).pw_name
    except KeyError:
        return getpass.getuser()


def unit_values(st: S.Settings, user: Optional[str] = None, data_dir: Optional[Path] = None,
                python: Optional[str] = None, path_env: Optional[str] = None,
                allow_root: bool = False, check_user: bool = True, home: Optional[str] = None,
                npm_bin: Optional[str] = None) -> dict:
    """The placeholder values for this install, from Settings (what `orchestra up` would use)."""
    user = user or default_user()
    if user == "root" and not allow_root:
        raise UnitError("refusing User=root: the services and the seats run as a normal user "
                        "(docs/INSTALL.md section 0). Run as that user, or pass --user NAME.")
    pw_home = None
    if check_user:
        try:
            pw_home = pwd.getpwnam(user).pw_dir
        except KeyError:
            raise UnitError(f"no such user {user!r} on this host") from None
    # The SERVICE user's home (not root's, under sudo): ~/.local/bin is theirs.
    home = home or pw_home or os.path.expanduser("~")
    root = Path(st.repo_root).resolve()
    data = Path(os.path.expanduser(str(data_dir or st.data_dir)))
    data = data if data.is_absolute() else (Path.cwd() / data)
    py = python or st.python_bin()
    if not os.path.isabs(py):
        found = shutil.which(py)
        if not found:
            raise UnitError(f"python interpreter {py!r} not found")
        py = found
    return {
        "ORCHESTRA_USER": user,
        "ORCHESTRA_ROOT": str(root),
        "ORCHESTRA_DIR": str(data),
        "ORCHESTRA_CONFIG": str(Path(st.config_path).resolve()),
        "PYTHON": str(py),
        "PATH": build_path(os.environ.get("PATH", "") if path_env is None else path_env, home,
                           npm_bin=_npm_global_bin() if npm_bin is None else (npm_bin or None)),
    }


def select_units(sets: list) -> list:
    units = []
    for s in sets or ["stack"]:
        if s not in UNIT_SETS:
            raise UnitError(f"unknown unit set {s!r} (choose from {', '.join(UNIT_SETS)})")
        for u in UNIT_SETS[s]:
            if u not in units:
                units.append(u)
    return units


def double_telemetry_conflict(units: list, telemetry_enabled: bool, dest: Path) -> Optional[str]:
    """Why this install would run telemetryd twice on one data dir, or None."""
    if not telemetry_enabled:
        return None
    stack = STACK_UNIT in units or (Path(dest) / STACK_UNIT).exists()
    tele = TELEMETRYD_UNIT in units or (Path(dest) / TELEMETRYD_UNIT).exists()
    if not (stack and tele):
        return None
    return ("orchestra.service runs `orchestra up`, which already supervises telemetryd "
            "([telemetry] enabled = true in orchestra.toml). Installing orchestra-telemetryd.service "
            "as well would start a second daemon on the same data dir; it would lose the flock and "
            "restart every few seconds. Either set `[telemetry] enabled = false` in orchestra.toml "
            "(the unit then owns telemetryd), or install only one of them. --force overrides.")


@dataclass
class Plan:
    units: list
    values: dict
    rendered: dict                 # unit -> text
    dest: Path
    staging: Path
    enable: bool
    now: bool
    notes: list = field(default_factory=list)

    def commands(self, sudo: bool) -> list:
        """The shell commands that finish the install from the staged files."""
        pre = ["sudo"] if sudo else []
        cmds = [pre + ["install", "-m", "0644"] + [str(self.staging / u) for u in self.units]
                + [str(self.dest) + "/"]]
        cmds.append(pre + ["systemctl", "daemon-reload"])
        if self.enable:
            cmds.append(pre + ["systemctl", "enable"] + (["--now"] if self.now else []) + list(self.units))
        return cmds


def _q(arg: str) -> str:
    import shlex
    return shlex.quote(arg)


def render_commands(cmds: list) -> str:
    return "\n".join("  " + " ".join(_q(a) for a in c) for c in cmds)


def build_plan(st: S.Settings, sets: list, *, user=None, data_dir=None, python=None,
               dest: Path = DEFAULT_DEST, enable=True, now=False, force=False,
               allow_root=False, path_env=None, check_user=True, home=None, npm_bin=None,
               which=shutil.which) -> Plan:
    units = select_units(sets)
    conflict = double_telemetry_conflict(units, st.telemetry_enabled, dest)
    notes = []
    if conflict:
        if not force:
            raise UnitError(conflict)
        notes.append("WARNING (--force): " + conflict)
    values = unit_values(st, user=user, data_dir=data_dir, python=python, path_env=path_env,
                         allow_root=allow_root, check_user=check_user, home=home, npm_bin=npm_bin)
    for runtime, cli in unresolved_clis(runtime_clis(st), values["PATH"], which=which):
        notes.append(f"WARNING: `{cli}` ({runtime}, in [runtimes] enabled) is not on the PATH the units "
                     f"will use, so seats started by them cannot launch it. Install it, or put its "
                     f"directory on your PATH and re-run `orchestra install-unit`. Unit PATH: {values['PATH']}")
    rendered = {u: render_unit(st.repo_root, u, values) for u in units}
    staging = Path(values["ORCHESTRA_DIR"]) / "systemd"
    return Plan(units=units, values=values, rendered=rendered, dest=Path(dest), staging=staging,
                enable=enable, now=now, notes=notes)


Runner = Callable[[list], int]


def _default_runner(cmd: list) -> int:
    return subprocess.call(cmd)


def _sudo_usable(interactive: bool) -> bool:
    if not shutil.which("sudo"):
        return False
    if interactive:
        return True            # sudo will prompt for the password in this terminal
    return subprocess.call(["sudo", "-n", "true"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL) == 0


def execute(plan: Plan, *, dry_run=False, use_sudo=True, euid=None, interactive=None,
            runner: Runner = _default_runner, sudo_usable: Callable[[bool], bool] = _sudo_usable,
            out=print) -> int:
    """Stage, then install. Returns 0 installed (or dry run / staged on request), 1 failed."""
    euid = os.geteuid() if euid is None else euid
    interactive = sys.stdin.isatty() if interactive is None else interactive
    for n in plan.notes:
        out(n)
    out(f"units:    {', '.join(plan.units)}")
    out(f"user:     {plan.values['ORCHESTRA_USER']}")
    out(f"checkout: {plan.values['ORCHESTRA_ROOT']}")
    out(f"data dir: {plan.values['ORCHESTRA_DIR']}")
    out(f"python:   {plan.values['PYTHON']}")
    out(f"dest:     {plan.dest}")
    direct = euid == 0 or (plan.dest.is_dir() and os.access(plan.dest, os.W_OK))
    if dry_run:
        out(f"\n[dry-run] would write the rendered units to {plan.staging}/ and then run:")
        out(render_commands(plan.commands(sudo=not direct)))
        out("[dry-run] nothing written. `orchestra install-unit --print` shows the rendered files.")
        return 0
    plan.staging.mkdir(parents=True, exist_ok=True)
    for u, text in plan.rendered.items():
        (plan.staging / u).write_text(text)
    out(f"\nrendered: {', '.join(str(plan.staging / u) for u in plan.units)}")
    if direct:
        cmds = plan.commands(sudo=False)
    elif use_sudo and sudo_usable(interactive):
        cmds = plan.commands(sudo=True)
    else:
        why = "--no-sudo" if not use_sudo else "sudo is not available without a password here"
        out(f"\nNOT installed ({why}). Finish with these commands (no editing needed):")
        out(render_commands(plan.commands(sudo=True)))
        return 0 if not use_sudo else 1
    for c in cmds:
        out("+ " + " ".join(_q(a) for a in c))
        rc = runner(c)
        if rc != 0:
            out(f"failed (rc={rc}): {' '.join(c)}")
            return 1
    if plan.enable:
        started = "started and " if plan.now else ""
        out(f"\n{started}enabled: {', '.join(plan.units)} (they start on every boot)")
        if not plan.now and STACK_UNIT in plan.units:
            out("start now:  orchestra down; sudo systemctl start orchestra orchestra-recovery")
    else:
        out("\ninstalled, not enabled (--no-enable)")
    out("status:     systemctl status " + " ".join(plan.units))
    return 0


def cmd_install_unit(ns) -> int:
    from . import supervisor as SV
    st = S.load_settings()
    try:
        plan = build_plan(st, ns.unit, user=ns.user, data_dir=Path(ns.data_dir) if ns.data_dir else None,
                          python=ns.python, dest=Path(ns.dest), enable=not ns.no_enable, now=ns.now,
                          force=ns.force, allow_root=ns.force)
    except (UnitError, OSError) as e:
        print(f"install-unit: {e}", file=sys.stderr)
        return 2
    if ns.print:
        for u in plan.units:
            print(f"# ---- {plan.dest / u}")
            print(plan.rendered[u], end="" if plan.rendered[u].endswith("\n") else "\n")
        return 0
    if ns.now and STACK_UNIT in plan.units and not ns.dry_run:
        status = SV.read_status(Path(plan.values["ORCHESTRA_DIR"]))
        if status["running"]:
            print(f"install-unit: a supervisor is already running (pid {status['pid']}); "
                  "orchestra.service would refuse to start next to it. Run `orchestra down` "
                  "first, or drop --now.", file=sys.stderr)
            return 2
    return execute(plan, dry_run=ns.dry_run, use_sudo=not ns.no_sudo)
