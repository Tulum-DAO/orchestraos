"""`orchestra doctor` — one table of checks, OK / MISSING / WARN / INFO, a one-line
remedy each, exit 1 when anything REQUIRED is MISSING. Pure over DoctorProbes so it
runs hermetically under test (no CLIs, no sockets, no tmux)."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import runtime_probe as RP
from .settings import Settings, missing_required

OK, MISSING, WARN, INFO = "OK", "MISSING", "WARN", "INFO"
ROTATION_TIERS = ("T2",)              # cron_beat.ARMED_TIERS — wave-1 armed tier
RETIRED_STATES = ("retired", "quiescent", "archived", "parked")


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    remedy: str = ""
    required: bool = True


@dataclass
class DoctorProbes:
    which: Callable[[str], Optional[str]]
    run_cmd: Callable[[list], str]
    port_owner: Callable[[int], Optional[int]]            # None = free, else owning pid (0 = unknown)
    supervisor_state: Callable[[Path], Optional[dict]]
    import_ok: Callable[[str], bool]
    read_file: Callable[[str], str]                         # raises FileNotFoundError when absent
    now_ms: Callable[[], int]
    python_version: tuple
    git_hooks_path: Callable[[Path], str]


# --- real probes -----------------------------------------------------------

def _port_owner_real(port: int) -> Optional[int]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return None
        except OSError:
            pass
    ss = shutil.which("ss")
    if ss:
        try:
            out = subprocess.run([ss, "-ltnp"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if re.search(rf":{port}\s", line):
                    m = re.search(r"pid=(\d+)", line)
                    if m:
                        return int(m.group(1))
        except Exception:  # noqa: BLE001
            pass
    return 0


def _supervisor_state_real(data_dir: Path) -> Optional[dict]:
    try:
        return json.loads((Path(data_dir) / "state" / "supervisor.json").read_text())
    except (FileNotFoundError, ValueError):
        return None


def _git_hooks_path_real(root: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(root), "config", "--get", "core.hooksPath"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def default_probes(st: Settings) -> DoctorProbes:
    py = st.python_bin()

    def _import_ok(mod: str) -> bool:
        if py == sys.executable:
            return importlib.util.find_spec(mod) is not None
        r = subprocess.run([py, "-c", f"import {mod}"], capture_output=True, timeout=30)
        return r.returncode == 0

    def _run(argv):
        return subprocess.run(argv, capture_output=True, text=True, timeout=20, check=True,
                              stdin=subprocess.DEVNULL).stdout

    return DoctorProbes(
        which=shutil.which, run_cmd=_run, port_owner=_port_owner_real,
        supervisor_state=_supervisor_state_real, import_ok=_import_ok,
        read_file=lambda p: Path(p).read_text(), now_ms=lambda: int(time.time() * 1000),
        python_version=tuple(sys.version_info[:3]), git_hooks_path=_git_hooks_path_real,
    )


# --- the checks ------------------------------------------------------------

def _exists(probes: DoctorProbes, path) -> bool:
    try:
        probes.read_file(str(path))
        return True
    except (FileNotFoundError, OSError):
        return False


def _runtime_field(entry: dict) -> str:
    rt = (entry.get("runtime") or "").strip().lower()
    if rt:
        return rt
    model = str(entry.get("model") or "").lower()
    if model.startswith("gemini"):
        return "gemini"
    if model.startswith(("gpt", "o1", "o3", "o4", "codex")):
        return "codex"
    return "claude"          # runtime_signatures DEFAULT_RUNTIME fail-safe


def run_doctor(st: Settings, probes: DoctorProbes) -> list:
    checks: list[Check] = []
    root = st.repo_root

    # -- host tools
    pv = probes.python_version
    checks.append(Check("python", OK if pv >= (3, 11) else MISSING,
                        f"{'.'.join(map(str, pv))} (need 3.11+ for tomllib)",
                        "Install Python 3.11+ (Ubuntu: apt install python3.12 python3.12-venv)"))
    for tool, remedy in (("tmux", "apt install tmux"),
                         ("node", "Install Node 22 (nodesource or nvm) — api + dashboard-proxy run on it"),
                         ("npm", "Install npm (ships with Node 22)")):
        path = probes.which(tool)
        checks.append(Check(tool, OK if path else MISSING, path or "not on PATH", remedy))

    # -- config
    if not st.config_exists:
        checks.append(Check("config", MISSING, f"no {st.config_path}",
                            "Run `orchestra init` (copies orchestra.example.toml) then edit it"))
    else:
        miss = missing_required(st.raw)
        if not st.raw:
            checks.append(Check("config", MISSING, f"{st.config_path} unreadable/empty",
                                "Fix the TOML (compare with orchestra.example.toml)"))
        elif miss:
            checks.append(Check("config", MISSING, "missing required keys: " + ", ".join(miss),
                                f"Set them in {st.config_path} (see orchestra.example.toml)"))
        else:
            checks.append(Check("config", OK, f"{st.config_path} ({len(REQUIRED_LABELS)} required keys present)"))

    # -- data dir
    if st.data_dir.is_dir() and os.access(st.data_dir, os.W_OK):
        checks.append(Check("data-dir", OK, str(st.data_dir)))
    else:
        checks.append(Check("data-dir", MISSING, f"{st.data_dir} missing or not writable",
                            "Run `orchestra init` (creates it) or fix [data] dir"))
    reg = st.data_dir / "registry.json"
    checks.append(Check("data:registry", OK if reg.exists() else WARN, str(reg),
                        "Run `orchestra init` (seeds an empty registry)", required=False))

    # -- gateway token
    tok_ok = st.token_file.exists() and bool(st.token_file.read_text().strip())
    checks.append(Check("gateway:token", OK if tok_ok else MISSING, str(st.token_file),
                        "Run `orchestra init` (writes it) — the gateway refuses to start without a bearer"))

    # -- runtimes (the SAME probe definitions the API serves at /api/runtimes/available)
    providers_path = root / "config" / "providers.json"
    results = []
    if providers_path.exists():
        deps = RP.ProbeDeps(which=probes.which, run_cmd=probes.run_cmd, read_file=probes.read_file,
                            now_ms=probes.now_ms, expand_home=os.path.expanduser)
        results = RP.probe_all(RP.load_providers(providers_path), st.runtimes_enabled, deps)
    any_authed = any(r["authed"] is True for r in results)
    for r in results:
        if r["authed"] is True:
            checks.append(Check(f"runtime:{r['id']}", OK, f"{r['cli']} installed + authed"))
            continue
        status = WARN if any_authed else MISSING
        if not r["installed"]:
            detail = f"{r['cli']} not installed"
            remedy = f"Install the {r['label']} CLI (`{r['cli']}`) or drop '{r['id']}' from [runtimes] enabled"
        else:
            detail = f"{r['cli']} installed, auth: {r['auth_reason'] or r['authed']}"
            remedy = f"Log in: run `{r['cli']}` once and complete its auth/login flow, then re-run doctor"
        checks.append(Check(f"runtime:{r['id']}", status, detail, remedy, required=(status == MISSING)))
    checks.append(Check("runtime:any", OK if any_authed else MISSING,
                        ", ".join(r["id"] for r in results if r["authed"] is True) or "no enabled runtime is installed AND authed",
                        "At least one of [runtimes] enabled must be installed and logged in (claude OR gemini OR codex)"))

    # -- ports
    sup = probes.supervisor_state(st.data_dir) or {}
    own = {}
    for name, ch in (sup.get("children") or {}).items():
        if ch.get("pid"):
            own[int(ch["pid"])] = name
    ports = [("gateway", st.gateway_port), ("api", st.api_port), ("dashboard", st.dashboard_port)]
    if st.arturo_enabled:
        ports.append(("arturo", st.arturo_port))
    for name, port in ports:
        owner = probes.port_owner(port)
        if owner is None:
            checks.append(Check(f"port:{name}", OK, f":{port} free"))
        elif owner in own:
            checks.append(Check(f"port:{name}", OK, f":{port} owned by orchestra:{own[owner]} (pid {owner})"))
        else:
            who = f"pid {owner}" if owner else "an unknown process"
            checks.append(Check(f"port:{name}", MISSING, f":{port} in use by {who}",
                                f"Stop that process or change [{name}] port in {st.config_path.name}"))

    # -- python deps (in the venv if one exists)
    checks.append(Check("python:aiohttp", OK if probes.import_ok("aiohttp") else MISSING,
                        "gateway (scripts/watch_gateway.py) needs it",
                        "Run `orchestra init` (venv + pip install -r requirements.txt)"))
    for mod in ("flask", "openai"):
        ok = probes.import_ok(mod)
        checks.append(Check(f"python:{mod}", OK if ok else (WARN if st.arturo_enabled else INFO),
                            "arturo voice brain needs it" + ("" if st.arturo_enabled else " ([arturo] enabled=false)"),
                            "Run `orchestra init` (pip install -r requirements.txt) or set [arturo] enabled=false",
                            required=False))

    # -- node deps + builds
    for label, sub in (("root", ""), ("api", "api"), ("dashboard", "dashboard")):
        d = root / sub if sub else root
        if not (d / "package.json").exists():
            continue
        nm = (d / "node_modules").exists()
        checks.append(Check(f"{label}:node_modules", OK if nm else MISSING, str(d / "node_modules"),
                            f"Run `orchestra init` or `cd {d} && npm install`"))
    for label, sub, artifact in (("api", "api", "dist/server.js"), ("dashboard", "dashboard", "dist/index.html")):
        d = root / sub
        if not (d / "package.json").exists():
            continue
        built = (d / artifact).exists()
        checks.append(Check(f"{label}:build", OK if built else MISSING, str(d / artifact),
                            f"Run `orchestra init` or `cd {d} && npm run build`"))

    # -- rotation beat (default ON) + eligible seats
    kill = st.runtime_dir / "FLEET_BEAT_DISABLED"
    if not st.beat_enabled:
        checks.append(Check("rotation:beat", WARN, "disabled: [rotation] beat_enabled=false",
                            "Set beat_enabled=true — autonomous blue-green rotation ships ON by default",
                            required=False))
    elif _exists(probes, kill):
        checks.append(Check("rotation:beat", WARN, f"e-brake present: {kill} (FLEET_BEAT_DISABLED)",
                            f"rm {kill} to let cron_beat act again", required=False))
    else:
        checks.append(Check("rotation:beat", OK,
                            f"armed: cron_beat every {st.cron_beat_interval}s, bus_beat every {st.bus_beat_interval}s, "
                            f"boundary_delivery {'armed' if st.boundary_delivery_armed else 'shadow'}",
                            "", required=False))
    armed_roots = []
    try:
        for line in probes.read_file(str(st.runtime_dir / "self_retire_armed")).splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                armed_roots.append(line)
    except (FileNotFoundError, OSError):
        pass
    eligible = []
    try:
        agents = json.loads(reg.read_text()).get("agents", {}) or {}
        for aid, entry in agents.items():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("tier", "")).upper() not in ROTATION_TIERS:
                continue
            if _runtime_field(entry) != "claude":
                continue
            if str(entry.get("status", "")).lower() in RETIRED_STATES:
                continue
            eligible.append(aid)
    except (FileNotFoundError, ValueError):
        pass
    checks.append(Check("rotation:seats", INFO,
                        (f"eligible (tier T2, claude runtime, live): {', '.join(sorted(eligible))}"
                         if eligible else "no eligible seats yet (register a T2 claude seat)")
                        + f"; armed allowlist ({st.runtime_dir / 'self_retire_armed'}): "
                        + (", ".join(a for a in armed_roots if a in eligible or not eligible) or "empty"),
                        "One lineage root per line in the allowlist enables hard rotation for that seat",
                        required=False))

    # -- git hooks (secret scan before push)
    hp = probes.git_hooks_path(root)
    checks.append(Check("git:hooks", OK if hp else WARN, hp or "core.hooksPath unset",
                        "git config core.hooksPath .git-hooks  (pre-push secret scan)", required=False))
    return checks


REQUIRED_LABELS = [".".join(k) for k in __import__("orchestra_cli.settings", fromlist=["REQUIRED_KEYS"]).REQUIRED_KEYS]


def exit_code(checks: list) -> int:
    return 1 if any(c.required and c.status == MISSING for c in checks) else 0


def render_table(checks: list) -> str:
    rows = [("CHECK", "STATUS", "DETAIL", "REMEDY")]
    for c in checks:
        rows.append((c.name, c.status, c.detail, c.remedy if c.status in (MISSING, WARN) else ""))
    w0 = max(len(r[0]) for r in rows)
    w1 = max(len(r[1]) for r in rows)
    w2 = min(max(len(r[2]) for r in rows), 70)
    out = []
    for r in rows:
        detail = r[2] if len(r[2]) <= w2 else r[2][:w2 - 1] + "…"
        line = f"{r[0].ljust(w0)}  {r[1].ljust(w1)}  {detail.ljust(w2)}"
        if r[3]:
            line += f"  -> {r[3]}"
        out.append(line.rstrip())
    n_missing = sum(1 for c in checks if c.status == MISSING and c.required)
    out.append("")
    out.append("doctor: " + ("all required checks OK" if n_missing == 0 else f"{n_missing} required check(s) MISSING"))
    return "\n".join(out)


def render_json(checks: list) -> str:
    return json.dumps({"ok": exit_code(checks) == 0,
                       "checks": [c.__dict__ for c in checks]}, indent=1)
