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
    http_get: Callable[[str], str] = None                    # raises on any failure; body text on 200
    tmux_sessions: Callable[[], list] = None                 # every tmux session name on this host
    env_get: Callable[[str], Optional[str]] = None           # os.environ.get unless a test injects one


# The agent CLI versions this release was PROVEN on (G22). The harness reads the CLI's screen,
# transcripts and hook events and does not heal itself when the CLI changes shape, so a version not
# listed here is a WARN (never MISSING: a mismatch must not block `orchestra up`). The FIRST entry is
# the pin docs/INSTALL.md and the Dockerfile install; update both with this list at each release.
# 2.1.295: the release gate + real captures (2026-10-09, menu context for the dashed-frame prompts);
# 2.1.284: the reference fleet.
PROVEN_CLI_VERSIONS = {"claude": ("2.1.295", "2.1.284")}
CLI_PACKAGES = {"claude": "@anthropic-ai/claude-code"}
_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


def cli_version_checks(probes: "DoctorProbes", enabled_ids) -> list:
    """One `cli:<id>-version` row per enabled runtime that has a proven-version list and is installed,
    plus `cli:autoupdater` when DISABLE_AUTOUPDATER is not set. All advisory (required=False)."""
    rows: list = []
    env_get = probes.env_get or os.environ.get
    checked_any = False
    for rid, proven in PROVEN_CLI_VERSIONS.items():
        if rid not in enabled_ids or not probes.which(rid):
            continue
        checked_any = True
        pin = f"sudo npm install -g {CLI_PACKAGES[rid]}@{proven[0]}"
        try:
            out = probes.run_cmd([rid, "--version"]) or ""
        except Exception:  # noqa: BLE001 — a probe failure is a WARN, never a crash
            out = ""
        m = _VERSION_RE.search(out)
        if not m:
            rows.append(Check(f"cli:{rid}-version", WARN, f"could not read `{rid} --version`", pin, required=False))
        elif m.group(1) in proven:
            rows.append(Check(f"cli:{rid}-version", OK, f"{m.group(1)} (proven: {', '.join(proven)})"))
        else:
            rows.append(Check(f"cli:{rid}-version", WARN,
                              f"{m.group(1)} is not a version this release was proven on ({', '.join(proven)}); "
                              f"the harness reads the CLI's screens and may misread a newer one",
                              pin, required=False))
    if checked_any and not env_get("DISABLE_AUTOUPDATER"):
        rows.append(Check("cli:autoupdater", WARN, "DISABLE_AUTOUPDATER is not set: the CLI can update itself off the pin",
                          "echo 'export DISABLE_AUTOUPDATER=1' >> ~/.bashrc && export DISABLE_AUTOUPDATER=1",
                          required=False))
    return rows


# --- real probes -----------------------------------------------------------

# The one wording for "you have not logged in to any runtime". `orchestra spawn` reuses this
# verbatim rather than inventing a second phrasing for the same condition.
RUNTIME_ANY_REMEDY = ("At least one of [runtimes] enabled must be installed and logged in "
                      "(claude OR gemini OR codex)")


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

    def _http_get(url: str) -> str:
        import urllib.request
        with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 — loopback only
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
            return r.read().decode("utf-8", "replace")

    def _tmux_sessions() -> list:
        try:
            out = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True,
                                 text=True, timeout=5, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return []
        return [l.strip() for l in out.stdout.splitlines() if l.strip()] if out.returncode == 0 else []

    return DoctorProbes(
        which=shutil.which, run_cmd=_run, port_owner=_port_owner_real,
        supervisor_state=_supervisor_state_real, import_ok=_import_ok,
        read_file=lambda p: Path(p).read_text(), now_ms=lambda: int(time.time() * 1000),
        python_version=tuple(sys.version_info[:3]), git_hooks_path=_git_hooks_path_real,
        http_get=_http_get, tmux_sessions=_tmux_sessions,
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


def statusline_check(hooks_mod, settings_path: Path) -> "Check":
    """The status line writes each Claude session's context reading. Without it the apps' context
    numbers (context_pct_of_window / _of_budget) stay empty and rotation reads only the screen.
    Advisory: OrchestraOS runs without it."""
    sl = hooks_mod.statusline_status(settings_path=settings_path)
    empty = "app context numbers will stay empty"
    if sl["state"] == "installed":
        return Check("statusline:claude", OK, f"installed: writes each session's context reading ({settings_path})",
                     required=False)
    if sl["state"] == "chained":
        return Check("statusline:claude", OK, "installed, chained to your own status line (shown unchanged); "
                     "writes each session's context reading", required=False)
    if sl["state"] == "theirs":
        return Check("statusline:claude", WARN, f"declined: your own status line is kept, so {empty}",
                     "`orchestra init` and answer yes to keep yours and add context tracking", required=False)
    if sl["state"] == "absent":
        return Check("statusline:claude", WARN, f"not installed, so {empty}", "`orchestra init` to install it",
                     required=False)
    return Check("statusline:claude", WARN, sl.get("error", "unreadable"), "fix the settings file", required=False)


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
    # ONE LEADING LOGIN ROW (gm acceptance 2026-10-06). With nothing authed, every enabled
    # runtime reports MISSING and so does `runtime:any`, so a stranger on a fresh install
    # reads "4 required check(s) MISSING" as a broken install when all it needs is ONE
    # login. Collapse the ASK into a single required row and demote the per-runtime rows to
    # advisory -- they are consequences of the same one fact, and they KEEP their MISSING
    # status and detail, so nothing is hidden. The GATE DOES NOT MOVE: this row is required
    # and MISSING, so `exit_code` is still non-zero until some CLI is authed.
    login_row = None
    if results and not any_authed:
        installed = [r for r in results if r["installed"]]
        pick = (installed or results)[0]
        if installed:
            detail = (f"no agent CLI is authed; {len(installed)} of {len(results)} "
                      f"enabled installed: {pick['cli']}")
            remedy = f"Run `{pick['cli']}` once, complete its login, then re-run doctor"
        else:
            detail = (f"no agent CLI is installed or authed "
                      f"({len(results)} enabled, 0 installed)")
            remedy = (f"Install one agent CLI and log in -- e.g. the {pick['label']} CLI "
                      f"(`{pick['cli']}`); see docs/INSTALL.md")
        login_row = Check("runtime:login", MISSING, detail, remedy)
        checks.append(login_row)
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
        # required only when it is the sole signal; the login row above owns the gate.
        checks.append(Check(f"runtime:{r['id']}", status, detail, remedy,
                            required=(status == MISSING and login_row is None)))
    # G22: the installed CLI versions against the ones this release was proven on
    checks.extend(cli_version_checks(probes, set(st.runtimes_enabled or ())))
    # Claude Code hooks installed into the user's settings (item: seats act on mail with no keypress)
    try:
        import sys as _sys
        _sys.path.insert(0, str(root / "hooks"))
        import install as _hooks  # noqa: WPS433
        settings_path = Path(os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude"))) / "settings.json"
        hs = _hooks.status(settings_path=settings_path, repo_root=root)
        if hs.get("error"):
            checks.append(Check("hooks:claude", WARN, hs["error"], "fix ~/.claude/settings.json, then `orchestra init`"))
        elif hs["missing"]:
            checks.append(Check("hooks:claude", WARN, f"{len(hs['missing'])} of {len(hs['installed']) + len(hs['missing'])} hook rows missing from {settings_path}",
                                "run `orchestra init` (idempotent) to install the shipped hooks"))
        else:
            checks.append(Check("hooks:claude", OK, f"{len(hs['installed'])} hook rows in {settings_path}"))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("hooks:claude", WARN, f"hook status unavailable: {e}"))
    try:
        checks.append(statusline_check(_hooks, settings_path))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("statusline:claude", WARN, f"status line status unavailable: {e}", required=False))
    # Channel plugins (plugins/): each reports its own row; disabled = INFO, never a failure.
    try:
        import sys as _sys
        if str(root) not in _sys.path:
            _sys.path.insert(0, str(root))
        import plugins as _plugins  # noqa: WPS433
        for name in _plugins.KNOWN:
            mod = _plugins.load(name)
            if mod is None:
                checks.append(Check(f"plugin:{name}", INFO, "not installed", None))
                continue
            ps = mod.status(st.raw)
            level = {"OK": OK, "INFO": INFO, "MISSING": MISSING, "WARN": WARN}.get(ps.get("level"), WARN)
            checks.append(Check(f"plugin:{name}", level, ps.get("detail", ""), ps.get("fix")))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("plugin:telegram", WARN, f"plugin status unavailable: {e}"))
    # Which brain Arturo will boot with (track T2): api (GEMINI_API_KEY) | runtime (first authed
    # CLI above) | none. Same selection table services/arturo/brain.py runs at startup.
    if st.arturo_enabled:
        try:
            import sys as _sys2
            if str(root) not in _sys2.path:
                _sys2.path.insert(0, str(root))
            from services.arturo import brain as _brain
            key = os.environ.get("GEMINI_API_KEY", "")
            b = _brain.select_brain(st.arturo_brain, key, results, runtime_model=st.arturo_runtime_model,
                                    api_factory=lambda k, m: _brain.NullBrain(f"api:{m}"))
            if b.kind == "runtime":
                checks.append(Check("arturo:brain", OK, f"runtime ({b.runtime} via `{b.cli}`, model {b.model}) — brain={st.arturo_brain}"))
            elif key and st.arturo_brain in ("auto", "api"):
                checks.append(Check("arturo:brain", OK, f"api (GEMINI_API_KEY set) — brain={st.arturo_brain}"))
            else:
                checks.append(Check("arturo:brain", WARN, f"none — brain={st.arturo_brain}: {getattr(b, 'reason', '')[:90]}",
                                    "Log in to one CLI (claude / codex / agy) or export GEMINI_API_KEY; Arturo still boots, text-only, and answers with this fix"))
        except Exception as e:  # noqa: BLE001
            checks.append(Check("arturo:brain", WARN, f"selection unavailable: {e}"))
    checks.append(Check("runtime:any", OK if any_authed else MISSING,
                        ", ".join(r["id"] for r in results if r["authed"] is True) or "no enabled runtime is installed AND authed",
                        RUNTIME_ANY_REMEDY, required=(login_row is None)))

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

    # -- api health (only while OUR supervisor owns the api port): process up + DB open through
    # the native binding, via GET /api/health. A bound port is not a serving api.
    api_owner = probes.port_owner(st.api_port)
    if api_owner is not None and api_owner in own and probes.http_get is not None:
        url = f"http://{st.api_host}:{st.api_port}/api/health"
        try:
            body = json.loads(probes.http_get(url))
            db_open = bool((body.get("db") or {}).get("open"))
            checks.append(Check("api:health", OK if db_open else MISSING,
                                f"{url} -> {body.get('status')} (db open={db_open})",
                                "" if db_open else "api is up but tasks.db does not answer: see <data>/logs/api.log"))
        except Exception as e:  # noqa: BLE001 — any failure = not serving
            checks.append(Check("api:health", MISSING, f"{url} failed: {str(e)[:80]}",
                                "api child is bound but not serving; see <data>/logs/api.log"))

    # -- tmux is host-global: say which sessions are NOT registered seats (ignored by the beat
    # and the dashboard since they became registry-scoped)
    if probes.tmux_sessions is not None:
        try:
            agents = json.loads(reg.read_text()).get("agents", {}) or {}
        except (FileNotFoundError, ValueError):
            agents = {}
        aliases = {e.get("tmux_session") for e in agents.values() if isinstance(e, dict) and e.get("tmux_session")}
        foreign = sorted(s for s in (probes.tmux_sessions() or []) if s not in agents and s not in aliases)
        if foreign:
            shown = ", ".join(foreign[:8]) + (f", +{len(foreign) - 8} more" if len(foreign) > 8 else "")
            checks.append(Check("tmux:foreign-sessions", WARN,
                                f"{len(foreign)} tmux session(s) on this host are not registered seats and are ignored: {shown}",
                                "Register them (registry-update.py) or run one OrchestraOS instance per host",
                                required=False))
        else:
            checks.append(Check("tmux:foreign-sessions", OK, "every tmux session on this host is a registered seat",
                                "", required=False))

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

    # -- arturo:local-stt (item C + P1-a): can the web mic fall back to on-box transcription with no key?
    # sherpa-onnx is in the DEFAULT install, so "not installed" means `orchestra init` did not finish
    # (WARN, never required); ready = OK and names engine + model.
    if st.arturo_enabled:
        try:
            out = probes.run_cmd([st.python_bin(), "-c",
                                  "import sys, json; sys.path.insert(0, %r); from services.arturo import local_stt as l; "
                                  "print(json.dumps(l.state()))" % str(root)])
            import json as _json
            stt = _json.loads(out.strip().splitlines()[-1])
        except Exception as e:  # noqa: BLE001
            stt = {"state": "error", "reason": f"probe failed: {e}"}
        state = stt.get("state")
        eng = f"{stt.get('engine', '?')} {stt.get('model', '')}".strip()
        if state == "ready":
            checks.append(Check("arturo:local-stt", OK, f"local speech-to-text ready ({eng}) — the web mic works in every browser, no key", "", required=False))
        elif state == "not-installed":
            checks.append(Check("arturo:local-stt", WARN, f"speech engine not installed ({eng}) — the web mic dictates only in browsers with on-device speech (Chrome/Edge/Safari)",
                                f"Run `{stt.get('install', 'orchestra init')}` (sherpa-onnx wheel ~15 MB + a ~99 MB model, fetched in the background)", required=False))
        elif state == "off":
            checks.append(Check("arturo:local-stt", INFO, "disabled (ARTURO_LOCAL_STT=0)", "", required=False))
        else:
            checks.append(Check("arturo:local-stt", WARN, f"{state} ({eng}): {stt.get('reason', '')}"[:120],
                                "Wait for the download (it retries at `orchestra up`), or run `orchestra init` to fetch the model now", required=False))

    # -- dashboard:https (item C): the microphone (and camera) only work in a secure context. A
    # dashboard bound to a LAN/VPN address over plain http has no mic in ANY browser.
    if st.dashboard_host not in ("127.0.0.1", "localhost", "::1"):
        checks.append(Check("dashboard:https", INFO,
                            f"dashboard bound to {st.dashboard_host} — browsers allow the microphone only over HTTPS or localhost",
                            "Front it with `tailscale serve` or a TLS reverse proxy (see docs/ARTURO.md § Dictation)", required=False))

    # -- orchestra:version (issue #104): installed vs the newest tag on origin. Behind is a
    # NUDGE (WARN, not required); a remote that cannot be asked is INFO, never a failure.
    from . import version as V

    def _git_via_probe(argv, cwd=None):
        return 0, probes.run_cmd(["git", "-C", str(cwd), *argv])
    try:
        vs = V.status(root, git=_git_via_probe)
    except Exception:  # noqa: BLE001
        vs = {"installed": None, "latest": None, "behind": None}
    if vs["behind"]:
        checks.append(Check("orchestra:version", WARN,
                            f"installed {vs['installed']}, latest {vs['latest']} on origin",
                            "run `orchestra upgrade` (then `orchestra down && orchestra up`)", required=False))
    elif vs["installed"] and vs["latest"]:
        checks.append(Check("orchestra:version", OK, f"{vs['installed']} (latest {vs['latest']})", required=False))
    else:
        checks.append(Check("orchestra:version", INFO,
                            f"installed {vs['installed'] or 'unknown'}; latest unknown (origin not reachable or no tags)",
                            None, required=False))

    # -- node deps + builds
    for label, sub in (("root", ""), ("api", "api"), ("dashboard", "dashboard")):
        d = root / sub if sub else root
        if not (d / "package.json").exists():
            continue
        # The same rule init uses: installed = init's completion stamp matches the lockfile. A
        # node_modules left half-filled by a killed install exists but is not installed.
        from .init_cmd import _deps_installed
        ok = _deps_installed(d)
        detail = str(d / "node_modules") if ok else (
            f"{d / 'node_modules'} is incomplete or out of date (interrupted or older install)"
            if (d / "node_modules").exists() else str(d / "node_modules"))
        checks.append(Check(f"{label}:node_modules", OK if ok else MISSING, detail, "Run `orchestra init`"))
    # better-sqlite3 is a native addon: an `npm ci` that skipped the prebuild leaves
    # node_modules present but no better_sqlite3.node, and the api crash-loops under the
    # supervisor ("Could not locate the bindings file", misread by its recovery path as a
    # corrupt tasks.db). Load it the way dist/server.js will.
    api_nm = root / "api" / "node_modules"
    if (root / "api" / "package.json").exists() and api_nm.exists():
        mod = api_nm / "better-sqlite3"
        try:
            probes.run_cmd(["node", "-e", f"require({json.dumps(str(mod))})"])
            checks.append(Check("api:better-sqlite3", OK, "native binding loads"))
        except Exception as e:  # noqa: BLE001 — any failure to load = MISSING
            checks.append(Check("api:better-sqlite3", MISSING, f"native binding does not load: {str(e)[:80]}",
                                f"cd {root / 'api'} && npm rebuild better-sqlite3  (or delete api/node_modules and run npm install)"))
    # node-pty is the native addon behind the web terminal (dashboard-proxy.js and api
    # /ws/terminal both require() it from the root install). On a host without the build
    # toolchain, npm finishes with node-pty skipped or unbuilt, the app boots green, and every
    # terminal pane answers "Web terminal unavailable: node-pty is not installed" (second-install
    # DX report, 2026-09-20). Load it the way both servers will; REQUIRED, so `doctor` exits 1.
    root_nm = root / "node_modules"
    if (root / "package.json").exists() and root_nm.exists():
        mod = root_nm / "node-pty"
        try:
            probes.run_cmd(["node", "-e", f"require({json.dumps(str(mod))})"])
            checks.append(Check("terminal:node-pty", OK, "native addon loads (web terminal available)"))
        except Exception as e:  # noqa: BLE001 — any failure to load = MISSING
            checks.append(Check("terminal:node-pty", MISSING,
                                f"native addon does not load: {str(e)[:80]} — the web terminal is dead on this host",
                                f"sudo apt install -y build-essential python3 && cd {root} && npm rebuild node-pty  "
                                f"(or delete node_modules and run npm install)"))
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
    # An ADVISORY MISSING row is marked `MISSING*`. Without it the footer could read
    # "1 required check(s) MISSING" above four red rows, which is the same "my install is
    # broken" reading the consolidated login row exists to remove. RENDER-ONLY: `c.status`
    # is untouched, so `--json` and every status assertion see the same values as before.
    rows = [("CHECK", "STATUS", "DETAIL", "REMEDY")]
    advisory = False
    for c in checks:
        token = c.status
        if c.status == MISSING and not c.required:
            token, advisory = MISSING + "*", True
        rows.append((c.name, token, c.detail, c.remedy if c.status in (MISSING, WARN) else ""))
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
    if advisory:
        out.append("  * advisory — not counted below; clears when the required row(s) are resolved.")
    out.append("doctor: " + ("all required checks OK" if n_missing == 0 else f"{n_missing} required check(s) MISSING"))
    return "\n".join(out)


def render_json(checks: list) -> str:
    return json.dumps({"ok": exit_code(checks) == 0,
                       "checks": [c.__dict__ for c in checks]}, indent=1)
