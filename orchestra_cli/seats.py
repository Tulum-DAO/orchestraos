"""orchestra spawn <seat> [--gm] / orchestra rotate <seat> — the two seat verbs.

spawn: register the seat in <data>/registry.json when absent (runtime, model, tier, prompt),
run spawn-agent.sh under the child env (ORCHESTRA_DIR = data dir, AGENT_RUNTIME/AGENT_MODEL
from the row), then verify by effect that the tmux session exists.

rotate: refuse without a banked handoff (<data>/docs/HANDOFF_<seat>-next.md carrying a
canary_questions block) unless --synthesize writes a minimal one, then run
scripts/rotate_agent.py (spawn successor, successor authors its readback, strict grade,
promote, rename panes) under the same env.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import settings as S

DEFAULT_MODEL = {"claude": "claude-opus-5[1m]", "gemini": "gemini-3.7-flash", "codex": "gpt-5.6-terra",
                 "grok": "grok-4.7"}


def _run(argv, env=None, cwd=None) -> int:
    return subprocess.run(argv, env=env, cwd=cwd).returncode


def _tmux_has_session(name: str) -> bool:
    return subprocess.run(["tmux", "has-session", "-t", name], capture_output=True).returncode == 0


def _registry(st: S.Settings) -> tuple[Path, dict]:
    p = st.data_dir / "registry.json"
    try:
        data = json.loads(p.read_text()) if p.exists() else {"agents": {}}
    except ValueError:
        data = {"agents": {}}
    data.setdefault("agents", {})
    return p, data


def _stored_agents(st: S.Settings) -> dict | None:
    """The armed identity store's seat rows (registry.json is only their projection and can lag),
    or None when the store is not armed or not part of this checkout."""
    if not (st.repo_root / "scripts" / "identity_store" / "cutover.py").exists():
        return None
    from .identity_cmd import stored_agents
    return stored_agents(st.repo_root, st.data_dir)


def _seat_row(st: S.Settings, seat: str) -> dict | None:
    """The seat's row from the source of truth: the armed store (None when it has none, whatever a
    lagging registry.json says), else registry.json."""
    stored = _stored_agents(st)
    if stored is not None:
        return stored.get(seat)
    return _registry(st)[1]["agents"].get(seat)


def _tier_rule(st: S.Settings):
    scripts = str(st.repo_root / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import tier_rule
    return tier_rule


def register_seat(st: S.Settings, seat: str, *, gm: bool, runtime: str | None, model: str | None,
                  tier: str | None, prompt: str | None, cwd: str | None = None,
                  parent: str | None = None, role: str | None = None) -> dict:
    """Idempotent: an existing row is kept verbatim (only missing fields are filled).

    A NEW non-gm seat gets its tier from its position (scripts/tier_rule.py): no parent or a T0
    parent -> T1, any other parent -> that parent's T2 (with reports_to), and a role (pm | worker,
    default worker). Raises tier_rule.TierRefused for an unregistered parent or a T2 request with no
    non-T0 parent; nothing is written then."""
    p, reg = _registry(st)
    existing = _seat_row(st, seat)                 # armed: the store's row, not a stale projection
    row = dict(existing or {})
    if existing is None and not gm:
        T = _tier_rule(st)
        prow = reg["agents"].get(parent) if parent else None
        tier, reports_to, warning = T.tier_for(seat, parent, prow is not None,
                                               (prow or {}).get("tier"), tier)
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
        if reports_to:
            row["reports_to"] = reports_to
        role = (role or "worker").strip().lower()
        if role not in T.ROLES:
            raise T.TierRefused(f"{seat}: unknown role '{role}' (one of {', '.join(T.ROLES)})")
        row["role"] = role
    runtime = runtime or row.get("runtime") or default_runtime(st)
    default_prompt = "prompts/gm.md" if gm else f"prompts/{seat}.md"
    row.setdefault("name", seat)
    row.setdefault("runtime", runtime)
    if model:
        row["model"] = model
    row.setdefault("model", DEFAULT_MODEL.get(row["runtime"], ""))
    # T0 = the always-on manager seat; below it tier is POSITION (T1 = reports to a T0 or to no one,
    # T2 = reports to a lead), and role says who runs the PM briefing (docs/REFERENCE_INSTALL.md).
    row.setdefault("tier", tier or ("T0" if gm else "T2"))
    # "vps" is the codebase's name for THIS host: spawn-agent.sh registers it, boot recovery
    # resumes only machine == "vps", and the reconcilers skip any other value as remote.
    # "local" here meant every starter seat was skipped by recovery after a reboot.
    row.setdefault("machine", "vps")
    row.setdefault("cwd", cwd or str(st.repo_root))
    row.setdefault("tmux_session", seat)
    row.setdefault("system_prompt", prompt or default_prompt)
    row.setdefault("always_on", bool(gm))
    row.setdefault("generation", 1)
    row.setdefault("status", "provisioning")
    reg["agents"][seat] = row
    p.parent.mkdir(parents=True, exist_ok=True)
    S.guard_test_write(p, "registry.json"); tmp = p.with_suffix(".json.tmp"); tmp.write_text(json.dumps(reg, indent=2) + "\n"); os.replace(tmp, p)
    _record_armed(st, seat, row)
    return row


def runtime_mismatch(st: S.Settings, seat: str, requested: str | None) -> str | None:
    """The refusal for an explicit --runtime that disagrees with the seat's stored row, else None.
    register_seat keeps an existing row verbatim, so without this the seat would pass the checks
    for the requested runtime and then start on the stored one."""
    if not requested:
        return None
    stored = (_seat_row(st, seat) or {}).get("runtime")
    if not stored or stored == requested:
        return None
    return (f"seat {seat} is registered as {stored}, not {requested}; starting it again would run "
            f"{stored}. To change its runtime: `orchestra retire {seat}` (keeps its session and "
            f"transcript), then spawn it again with --runtime {requested}.")


def retire_seat(st: S.Settings, seat: str, *, reason: str = "retired by the operator") -> dict:
    """Retire one seat, the way park-idle does: its registry row is removed (so no recovery or
    reconciler brings it back), its session entry is marked retired but keeps session_id,
    resume_command and the transcript path (it can be resumed by hand), and its tmux session is
    stopped. With the identity store armed the retire goes through the database in one
    transaction. Raises KeyError for a seat that is in neither the registry nor the sessions."""
    from datetime import datetime, timezone
    p, reg = _registry(st)
    sp = st.data_dir / "state" / "agent-sessions.json"
    try:
        meta = json.loads(sp.read_text())
    except (OSError, ValueError):
        meta = {}
    row = reg["agents"].get(seat)
    from .identity_cmd import stored_seat
    in_store = stored_seat(st.repo_root, st.data_dir, seat) if (st.repo_root / "scripts" / "identity_store" / "cutover.py").exists() else None
    if in_store is not None:                                # armed: the store is the truth, the files may lag
        row = in_store[0] or row
        if in_store[1] and seat not in meta:
            meta = dict(meta, **{seat: in_store[1]})
    if row is None and seat not in meta:
        raise KeyError(seat)
    retired = {"status": "retired", "retired_at": datetime.now(timezone.utc).isoformat(),
               "retired_by": "orchestra retire", "retired_reason": reason}
    entry = dict(meta.get(seat) or {}, **retired)
    from .identity_cmd import retire_seat_in_store
    stored = retire_seat_in_store(st.repo_root, st.data_dir, seat, reason, retired, dict(meta.get(seat) or {}))
    armed = stored is not None
    if armed:
        entry = stored
    else:
        reg["agents"].pop(seat, None)
        meta[seat] = entry
        S.guard_test_write(p, "registry.json")
        for path, obj in ((p, reg), (sp, meta)):
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp"); tmp.write_text(json.dumps(obj, indent=2) + "\n"); os.replace(tmp, path)
    # Stop the seat's OWN session only: its row's tmux_session (the seat name when there is no row),
    # and never one another registered seat says is its own.
    target = (row or {}).get("tmux_session") or (meta.get(seat) or {}).get("tmux_session") or seat
    # a session either the flat rows or (armed) the store's rows give to another seat is not ours
    others = {(r or {}).get("tmux_session") or name
              for rows in (reg["agents"], _stored_agents(st) or {}) for name, r in rows.items() if name != seat}
    stopped = []
    if target not in others:
        subprocess.run(["tmux", "kill-session", "-t", f"={target}"], capture_output=True, check=False)
        stopped.append(target)
    return {"seat": seat, "armed": armed, "session_id": entry.get("session_id"),
            "transcript": entry.get("conversation_path") or None, "tmux": stopped,
            "tmux_kept": None if stopped else target}


def cmd_retire(ns) -> int:
    st = S.load_settings()
    if not st.config_exists:
        print(f"no config at {st.config_path} — run `orchestra init` first", file=sys.stderr); return 2
    seat = ns.seat
    if not ns.yes:
        if not sys.stdin.isatty():
            print(f"retire refused: pass --yes to retire {seat} without a prompt", file=sys.stderr); return 2
        answer = input(f"Retire {seat}? Its row is removed and its tmux session stopped; "
                       f"its session and transcript are kept. [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("not retired"); return 1
    try:
        res = retire_seat(st, seat, reason=ns.reason)
    except KeyError:
        print(f"retire refused: no seat named {seat} in {st.data_dir}", file=sys.stderr); return 2
    if res["tmux_kept"]:
        print(f"warning: tmux session {res['tmux_kept']} is also another seat's; left running", file=sys.stderr)
    print(f"retired {seat}: row removed" + (", tmux session stopped" if res["tmux"] else "")
          + (f"; session {res['session_id']} kept" if res["session_id"] else "")
          + (f", transcript {res['transcript']}" if res["transcript"] else "") + ".")
    print(f"start it again (any runtime): orchestra spawn {seat} --runtime <runtime>")
    return 0


def _record_armed(st: S.Settings, seat: str, row: dict) -> None:
    """With the identity store armed, registry.json is a projection the regenerator rewrites
    every few seconds: a row written only there is lost. Record it in the database too."""
    if not (st.repo_root / "scripts" / "identity_store" / "cutover.py").exists():
        return
    from .identity_cmd import record_seat
    record_seat(st.repo_root, st.data_dir, seat, row)


def _local_runtime_facts(st: S.Settings) -> tuple[dict, list]:
    """(unusable, logged_in) from LOCAL evidence only (PR 0, gm condition 3): `which` plus reading
    each CLI's own login file, no command runs, no network (`claude auth status` makes HTTPS calls,
    measured), so this is fast and bounded.
      unusable  = {runtime id: plain reason} for every enabled runtime that evidence proves cannot
                  run a seat here: its CLI is not installed, or its login is a FILE probe (codex,
                  gemini) and that file says logged out.
      logged_in = enabled runtime ids, in [runtimes] enabled order, that are installed AND whose
                  login FILE says logged in (file-proven; claude can never be in it).
    A command-probed runtime (claude) that is installed is never in either: its only local signal
    is a fallback file that can be stale either way. Any error -> ({}, []) (nothing is proven;
    callers then behave exactly as before)."""
    try:
        from . import runtime_probe as RP
        providers = st.repo_root / "config" / "providers.json"
        if not providers.exists():
            return {}, []

        def _no_commands(argv):
            raise RuntimeError("local probe: commands are not run")

        def _read(path):
            with open(path, encoding="utf-8") as f:
                return f.read()

        import shutil
        import time
        deps = RP.ProbeDeps(which=shutil.which, run_cmd=_no_commands, read_file=_read,
                            now_ms=lambda: int(time.time() * 1000), expand_home=os.path.expanduser)
        enabled = st.runtimes_enabled or []
        out, authed = {}, []
        for p in RP.load_providers(providers):
            if p["id"] not in enabled:
                continue
            if not deps.which(p["cli"]):
                out[p["id"]] = f"`{p['cli']}` is not installed on this machine"
                continue
            if (p.get("auth_probe") or {}).get("kind", "").startswith("file-"):
                r = RP.run_auth_probe(p["auth_probe"], deps)
                if r.get("authed") is False:
                    out[p["id"]] = (f"`{p['cli']}` is installed but not logged in "
                                    f"({r.get('auth_reason')} in {p['auth_probe'].get('path')})")
                elif r.get("authed") is True:
                    authed.append(p["id"])
        return out, sorted(authed, key=enabled.index)
    except Exception:  # noqa: BLE001 — no proof of anything; never change the pick on doubt
        return {}, []


def _local_unusable(st: S.Settings) -> dict:
    """{runtime id: plain reason} for every enabled runtime that LOCAL evidence proves cannot run a
    seat on this machine (see _local_runtime_facts). An installed claude is never listed."""
    return _local_runtime_facts(st)[0]


# Ways Claude Code can be signed in that ~/.claude/.credentials.json does not show. Any one of
# them present means "maybe signed in", and the pick stays on claude without running anything.
_CLAUDE_AUTH_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
_CLAUDE_AUTH_SETTINGS = ("apiKeyHelper", "awsAuthRefresh", "awsCredentialExport")
CLAUDE_AUTH_STATUS_TIMEOUT_S = 5


def _platform() -> str:
    return sys.platform


def _claude_maybe_signed_in_locally() -> str | None:
    """Why claude MIGHT be signed in by a means a file check cannot rule out, or None when no local
    signal says so. Never proves "signed out"; it only decides whether asking the CLI is worth it.
    macOS keeps the login in the Keychain (no file), an API key or token can come from the env or
    from settings.json (its `env` block, apiKeyHelper, ...), and on Linux the OAuth login is
    <config dir>/.credentials.json. Anything unreadable counts as a signal: doubt keeps claude."""
    if _platform() == "darwin":
        return "macOS keeps Claude's login in the Keychain"
    for k in _CLAUDE_AUTH_ENV:
        if os.environ.get(k):
            return f"{k} is set"
    cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"))
    try:
        if (cfg / ".credentials.json").exists():
            return f"{cfg / '.credentials.json'} exists"
    except OSError:
        return "could not check Claude's credentials file"
    for name in ("settings.json", "settings.local.json"):
        try:
            data = json.loads((cfg / name).read_text())
        except FileNotFoundError:
            continue
        except Exception:  # noqa: BLE001 — unreadable settings: cannot rule a key out
            return f"{cfg / name} could not be read"
        if not isinstance(data, dict):
            return f"{cfg / name} is not a settings object"
        for key in _CLAUDE_AUTH_SETTINGS:
            if data.get(key):
                return f"{key} is set in {cfg / name}"
        env = data.get("env")
        if env is not None and not isinstance(env, dict):
            return f"{cfg / name} has an unreadable env block"
        for k in _CLAUDE_AUTH_ENV:
            if (env or {}).get(k):
                return f"{k} is set in {cfg / name}"
    return None


def _claude_auth_status() -> bool | None:
    """Claude Code's own answer: True / False only when `claude auth status --json` says so
    DEFINITIVELY, else None. A logged-out CLI prints {"loggedIn": false, "authMethod": "none", ...}
    and exits 1 (Claude Code 2.1.295, measured). Bounded by CLAUDE_AUTH_STATUS_TIMEOUT_S; a timeout,
    a CLI that cannot run, output that is not that JSON, or a "false" that names an auth method is
    None, and None keeps claude."""
    try:
        r = subprocess.run(["claude", "auth", "status", "--json"], capture_output=True, text=True,
                           timeout=CLAUDE_AUTH_STATUS_TIMEOUT_S, stdin=subprocess.DEVNULL)
        parsed = json.loads(r.stdout or "")
    except Exception:  # noqa: BLE001 — timeout, missing CLI, an old CLI without --json, not JSON
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("loggedIn"), bool):
        return None
    if parsed["loggedIn"]:
        return True
    if parsed.get("authMethod", "none") not in (None, "none"):
        return None                       # "logged out" yet naming a method: not definitive
    return False


def _signed_in_instead_of_claude(st: S.Settings, logged_in: list) -> str | None:
    """The runtime to use instead of a first-candidate claude, or None to keep claude (today's rule).
    Only when ALL hold: no local sign of a claude login (_claude_maybe_signed_in_locally, which
    keeps every macOS, API-key, token and credentials-file install on claude without running
    anything), ANOTHER enabled runtime is file-proven logged in, and only then `claude auth status`
    answers definitively that it is signed out. Any doubt or error keeps claude."""
    others = [rt for rt in logged_in if rt != "claude"]
    if not others:
        return None
    try:
        if _claude_maybe_signed_in_locally() is not None:
            return None
        if _claude_auth_status() is not False:
            return None
    except Exception:  # noqa: BLE001 — never change the pick on doubt
        return None
    print(f"note: claude is installed but signed out (`claude auth status`); "
          f"the new seat gets {others[0]}, which is logged in", file=sys.stderr)
    return others[0]


def default_runtime(st: S.Settings) -> str:
    """The runtime a new seat gets when none is named: the first entry of [runtimes] enabled that
    is not PROVEN unusable here (_local_unusable), with one more step for claude.

    claude's login cannot be read from a file everywhere, so an installed claude is never proven
    unusable locally. When it is the first candidate, it is kept unless _signed_in_instead_of_claude
    finds it DEFINITIVELY signed out (Linux, no credentials file, no API key or token in the env or
    settings, `claude auth status` says loggedIn false within 5 s) while another enabled runtime is
    file-proven logged in; then that runtime is picked. So a Codex-only box that also has the
    claude CLI installed gets a codex seat, not a claude seat stuck at its first-run screen, and a
    claude box (macOS, API key, signed in, or nothing else logged in) picks claude exactly as
    before. If every enabled runtime is unusable, the old rule (first enabled) stands and the
    login refusal explains."""
    enabled = st.runtimes_enabled or ["claude"]
    unusable, logged_in = _local_runtime_facts(st)
    candidates = [rt for rt in enabled if rt not in unusable]
    if not candidates:
        return enabled[0]
    if candidates[0] == "claude":
        return _signed_in_instead_of_claude(st, logged_in) or "claude"
    return candidates[0]


def _refuse_if_runtime_unusable(st: S.Settings, seat: str, runtime: str) -> int | None:
    """Refuse a spawn on a runtime local evidence proves cannot start, naming it and why."""
    why = _local_unusable(st).get(runtime)
    if why is None:
        return None
    login = {"codex": "`codex login` (on a server without a browser: `codex login --device-auth`)",
             "gemini": "`agy` once and complete its sign-in", "claude": "`claude` once and complete its login",
             "grok": "`grok login --device-auth` and complete its sign-in"}
    print(f"refusing to spawn {seat} on {runtime}: {why}.\n"
          f"  Install or log in to it (run {login.get(runtime, 'its CLI once')}), or pick another runtime with "
          f"--runtime.\n  `orchestra doctor` shows every runtime it found.", file=sys.stderr)
    return 2


def _authed_runtimes(st: S.Settings) -> tuple[list, str | None]:
    """(ids of authed runtimes, reason the answer is UNDETERMINED).

    Returns ([], None) ONLY on a positive determination that every enabled runtime was probed and
    none is authenticated. Anything else — no providers.json, a probe that raises, a provider whose
    auth is unknown rather than False — returns a reason, and the caller must then behave exactly as
    it did before this guard existed. A false refusal would break every spawn for everyone, which is
    a far worse failure than the confusing message this guard exists to prevent.
    """
    from . import doctor as D
    from . import runtime_probe as RP
    providers = st.repo_root / "config" / "providers.json"
    if not providers.exists():
        return [], f"no probe definitions at {providers}"
    dp = D.default_probes(st)   # the SAME probe callables doctor uses — one probe, not two
    deps = RP.ProbeDeps(which=dp.which, run_cmd=dp.run_cmd, read_file=dp.read_file,
                        now_ms=dp.now_ms, expand_home=os.path.expanduser)
    results = RP.probe_all(RP.load_providers(providers), st.runtimes_enabled, deps)
    if not results:
        return [], "no enabled runtime was probed"
    authed = [r["id"] for r in results if r.get("authed") is True]
    if authed:
        return authed, None
    # Every result must be a definite "not authed". A runtime that is not INSTALLED is definitely
    # not authenticated — that is a determination, not a doubt. Only an installed runtime whose auth
    # the probe could not read (authed is None) leaves the answer undetermined.
    undetermined = [r["id"] for r in results
                    if r.get("authed") is not False and r.get("installed") is not False]
    if undetermined:
        return [], f"auth undetermined for: {', '.join(undetermined)}"
    return [], None


def _refuse_if_no_runtime_authed(st: S.Settings) -> int | None:
    """Refuse the spawn when — and only when — nothing is authenticated. See _authed_runtimes."""
    from . import doctor as D
    try:
        authed, undetermined = _authed_runtimes(st)
    except Exception as e:  # noqa: BLE001 — fail OPEN: any doubt spawns exactly as before
        print(f"note: could not check runtime login ({str(e)[:80]}); spawning anyway", file=sys.stderr)
        return None
    if authed or undetermined:
        return None
    print("refusing to spawn: no enabled runtime is installed AND logged in.\n"
          f"  {D.RUNTIME_ANY_REMEDY}\n"
          "  Log in first (run `claude`, `codex login`, or `agy` once), then re-run this command.\n"
          "  `orchestra doctor` shows which runtimes it found.", file=sys.stderr)
    return 2


def _apply_verbose(ns) -> None:
    """--verbose: spawn-agent.sh prints its debugging detail (vlog). child_env copies os.environ."""
    if getattr(ns, "verbose", False):
        os.environ["ORCHESTRA_SPAWN_VERBOSE"] = "1"


def cmd_spawn(ns) -> int:
    _apply_verbose(ns)
    st = S.load_settings()
    if not st.config_exists:
        print(f"no config at {st.config_path} — run `orchestra init` first", file=sys.stderr); return 2
    refused = _refuse_if_no_runtime_authed(st)
    if refused is not None:
        return refused
    seat = ns.seat
    mismatch = runtime_mismatch(st, seat, ns.runtime)
    if mismatch:
        print(f"spawn refused: {mismatch}", file=sys.stderr); return 2
    runtime = ns.runtime or (_seat_row(st, seat) or {}).get("runtime") or default_runtime(st)
    refused = _refuse_if_runtime_unusable(st, seat, runtime)
    if refused is not None:
        return refused
    try:
        row = register_seat(st, seat, gm=ns.gm, runtime=runtime, model=ns.model, tier=ns.tier,
                            prompt=ns.prompt, parent=getattr(ns, "parent", None), role=getattr(ns, "role", None))
    except ValueError as e:      # tier_rule.TierRefused
        print(f"spawn refused: {e}", file=sys.stderr); return 2
    prompt_path = st.repo_root / row["system_prompt"]
    if not prompt_path.exists():
        print(f"warning: no role prompt at {prompt_path}; the seat boots on the foundation prompt only", file=sys.stderr)
    env = S.child_env(st)
    env["AGENT_RUNTIME"] = row["runtime"]
    if row.get("model"):
        env["AGENT_MODEL"] = row["model"]
    argv = [str(st.repo_root / "spawn-agent.sh"), seat]
    if ns.task:
        argv += ["--task", ns.task]
    rc = _run(argv, env=env, cwd=str(st.repo_root))
    if rc != 0:
        print(f"spawn-agent.sh exited {rc}", file=sys.stderr); return rc
    if not _tmux_has_session(row["tmux_session"]):
        print(f"spawn reported success but no tmux session '{row['tmux_session']}' exists (by effect); check {st.data_dir / 'logs'}", file=sys.stderr)
        return 1
    print(f"seat {seat} up: tmux session {row['tmux_session']} ({row['runtime']} {row.get('model', '')}, prompt {row['system_prompt']})")
    print(f"talk to it:   tmux attach -t {row['tmux_session']}\nmail it:      {mail_hint(st, seat)}")
    return 0


def mail_hint(st: S.Settings, seat: str) -> str:
    """A command that mails <seat> when pasted VERBATIM from any directory with nothing exported:
    it names msg_store.py by absolute path and carries the data dir itself. The old hint
    (`python3 msg_store.py send ...`) needed the checkout as cwd AND ORCHESTRA_DIR, because
    msg_store.py's own fallback is not this install's data dir (gate container, 2026-10-10:
    `unable to open database file`)."""
    import shlex
    return (f"ORCHESTRA_DIR={shlex.quote(str(st.data_dir))} python3 {shlex.quote(str(st.repo_root / 'msg_store.py'))} "
            f"send --from you --to {shlex.quote(seat)} --subject hi --body hello")


def _pane_alive(tmux_session: str) -> bool:
    """ALIVE = the session exists AND an agent CLI runs in its pane's process tree (issue #93:
    two 'spawned successfully' seats were dead). Not "any child of the pane shell": operator
    finding #10 (2026-10-08) was a gm whose pane ran a nested `tmux attach` over a shell, which
    that rule counted as a live gm, so starter skipped it. scripts/pane_cli.py answers from the
    process tree with the detector's own runtime rule; a nested client the CLI itself launched
    still has the CLI above it."""
    if not _tmux_has_session(tmux_session):
        return False
    scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import pane_cli
    st = pane_cli.pane_state(tmux_session)
    # An unreadable process table is UNKNOWN: report alive, so nothing relaunches over a seat
    # that may be running.
    return st["runtime"] is not None or st.get("tree") == "unknown"


_TEMPLATE_KINDS = {"dev": "prompts/_dev-template.md", "pm": "prompts/_pm-template.md",
                   "qa": "prompts/_qa-template.md"}
_TOKEN_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")


def fill_template(text: str, values: dict) -> tuple[str, list]:
    """Substitute {TOKEN}s; return (filled, still-missing tokens). Never guesses a value."""
    missing = sorted({t for t in _TOKEN_RE.findall(text) if t not in values})
    filled = _TOKEN_RE.sub(lambda m: str(values.get(m.group(1), m.group(0))), text)
    return filled, missing


def cmd_agent(ns) -> int:
    if ns.agent_command == "create":
        return cmd_agent_create(ns)
    print(f"unknown agent verb {ns.agent_command!r}", file=sys.stderr); return 2


def cmd_agent_create(ns) -> int:
    """issue #93: one verb instead of copy-template / sed / hand-edit registry.json / spawn.
    Order is fail-early: template fully filled -> runtime/model pair valid -> register (with
    reports_to) -> spawn -> ALIVE by effect. Nothing is written until the first two pass."""
    st = S.load_settings()
    if not st.config_exists:
        print(f"no config at {st.config_path} — run `orchestra init` first", file=sys.stderr); return 2
    name = ns.name
    mismatch = runtime_mismatch(st, name, ns.runtime)
    if mismatch:
        print(f"agent create refused: {mismatch}", file=sys.stderr); return 2
    runtime = ns.runtime or default_runtime(st)
    # 1. runtime/model pair (the same rule spawn-agent.sh refuses on; here it is a clean message)
    if ns.model:
        sys.path.insert(0, str(st.repo_root / "scripts")) if str(st.repo_root / "scripts") not in sys.path else None
        import runtime_signatures as rs
        try:
            rs.validate_model_for_runtime(runtime, ns.model, agent_id=name)
        except rs.RuntimeResolutionError as e:
            print(f"agent create refused: {e}", file=sys.stderr); return 2
    # 2. template -> prompts/<name>.md, every {TOKEN} filled or refuse
    prompt_rel = f"prompts/{name}.md"
    if ns.template:
        tpl_rel = _TEMPLATE_KINDS.get(ns.template, ns.template)
        tpl = st.repo_root / tpl_rel
        if not tpl.exists():
            print(f"agent create refused: no template at {tpl} (kinds: {', '.join(_TEMPLATE_KINDS)} or a path)", file=sys.stderr); return 2
        values = {"DEV_NAME": name, "PM_NAME": name, "YOUR_ID": name,
                  "CWD": getattr(ns, "cwd", None) or str(st.repo_root)}
        if ns.parent:
            values["PARENT_PM"] = ns.parent
        for kv in ns.set or []:
            if "=" not in kv:
                print(f"agent create refused: --set expects KEY=VALUE, got {kv!r}", file=sys.stderr); return 2
            k, v = kv.split("=", 1); values[k.strip()] = v
        filled, missing = fill_template(tpl.read_text(), values)
        if missing:
            print(f"agent create refused: template {tpl_rel} still has unfilled placeholders: "
                  f"{', '.join('{' + t + '}' for t in missing)} — pass --set {missing[0]}=... "
                  f"(and --parent for {{PARENT_PM}})", file=sys.stderr); return 2
        out = st.repo_root / prompt_rel
        if out.exists():
            print(f"agent create refused: {out} already exists (delete it or pick another name)", file=sys.stderr); return 2
        out.write_text(filled)
    elif not (st.repo_root / prompt_rel).exists():
        print(f"warning: no {prompt_rel} and no --template; the seat boots on the foundation prompt only", file=sys.stderr)
    # 3. register (+ parent), 4. spawn, 5. alive by effect
    refused = _refuse_if_no_runtime_authed(st)
    if refused is None:
        refused = _refuse_if_runtime_unusable(st, name, runtime)
    if refused is not None:
        return refused
    role = getattr(ns, "role", None) or ("pm" if ns.template == "pm" else None)
    try:
        row = register_seat(st, name, gm=False, runtime=runtime, model=ns.model, tier=ns.tier, prompt=prompt_rel,
                            cwd=getattr(ns, "cwd", None), parent=ns.parent, role=role)
    except ValueError as e:      # tier_rule.TierRefused
        print(f"agent create refused: {e}", file=sys.stderr); return 2
    if ns.parent and row.get("reports_to") != ns.parent:
        p, reg = _registry(st); reg["agents"][name]["reports_to"] = ns.parent; row["reports_to"] = ns.parent
        S.guard_test_write(p, "registry.json"); tmp = p.with_suffix(".json.tmp"); tmp.write_text(json.dumps(reg, indent=2) + "\n"); os.replace(tmp, p)
        _record_armed(st, name, reg["agents"][name])
    env = S.child_env(st); env["AGENT_RUNTIME"] = row["runtime"]
    if row.get("model"):
        env["AGENT_MODEL"] = row["model"]
    argv = [str(st.repo_root / "spawn-agent.sh"), name] + (["--task", ns.task] if ns.task else [])
    rc = _run(argv, env=env, cwd=str(st.repo_root))
    if rc != 0:
        print(f"spawn-agent.sh exited {rc} — the seat was NOT created successfully", file=sys.stderr); return rc
    if not _pane_alive(row["tmux_session"]):
        print(f"seat {name} is not alive by effect: tmux session {row['tmux_session']!r} "
              f"{'exists but runs no agent process' if _tmux_has_session(row['tmux_session']) else 'does not exist'}; "
              f"check {st.data_dir / 'logs'}", file=sys.stderr); return 1
    print(f"agent {name} created and alive: tmux {row['tmux_session']} ({row['runtime']} {row.get('model', '')}), "
          f"prompt {prompt_rel}, reports to {ns.parent or '-'}")
    print(f"talk to it:   tmux attach -t {row['tmux_session']}")
    return 0


def _pane_facts(st: S.Settings, tmux_session: str) -> tuple[str, str]:
    """(session id, pane id) of the predecessor from tmux + the pane-event file the hooks write."""
    try:
        r = subprocess.run(["tmux", "list-panes", "-t", tmux_session, "-F", "#{pane_id}"], capture_output=True, text=True, timeout=5)
        for pane in r.stdout.split():
            p = st.data_dir / "state" / "agent-events" / "panes" / f"{pane.lstrip('%')}.json"
            sid = (json.loads(p.read_text()) or {}).get("session_id") if p.exists() else None
            return (str(sid) if sid else "unknown", pane)
    except Exception:  # noqa: BLE001
        pass
    return "unknown", "unknown"


def _recent_mail_ids(st: S.Settings, seat: str, n: int = 2) -> list[str]:
    """Ids of the last messages delivered to the seat (they are in its transcript verbatim)."""
    import sqlite3
    db = st.data_dir / "state" / "tasks.db"
    if not db.exists():
        return []
    try:
        c = sqlite3.connect(str(db), timeout=3)
        rows = c.execute("select id from messages where to_agent=? and delivered_at is not null "
                         "order by delivered_at desc limit ?", (seat, n)).fetchall()
        c.close()
        return [r[0] for r in rows]
    except Exception:  # noqa: BLE001
        return []


def _synthesize_handoff(st: S.Settings, seat: str, row: dict) -> Path:
    """A minimal baton with ONE canary question that asks for several seat-specific literals
    at once (session id, generation, the two ports, the pane id / last mail ids, this file).
    Why one question: the strict grader's distinct-evidence rule wants each question to ground
    an anchor no OTHER answer cites; with three questions on a thin seat a successor that
    repeats the session id in every answer fails that rule on a coin flip (gate rerun
    2026-09-17, Finding 3). One question has no other answers to share with, so
    distinct_min == 1 is always reachable while every other threshold (>=2 grounded id-class
    anchors, region corroboration) still applies. Real batons are untouched."""
    docs = st.data_dir / "docs"; docs.mkdir(parents=True, exist_ok=True)
    p = docs / f"HANDOFF_{seat}-next.md"
    gen = int(row.get("generation", 1) or 1)
    sid, pane = _pane_facts(st, row.get("tmux_session", seat))
    prompt = st.repo_root / row.get("system_prompt", f"prompts/{seat}.md")
    mail = _recent_mail_ids(st, seat)
    q3_fact = (f"the last messages it received were {' and '.join(mail)}" if mail
               else f"it ran in tmux pane {pane}")
    q3_q = ("Which message ids did the predecessor receive most recently, and which file is this handoff?" if mail
            else "Which tmux pane did the predecessor run in, and which file is this handoff?")
    q3_a = (f"Messages {' and '.join(mail)}; handoff {p}; role prompt {prompt}." if mail
            else f"Pane {pane}; handoff {p}; role prompt {prompt}.")
    tasks_db = st.data_dir / "state" / "tasks.db"
    registry = st.data_dir / "registry.json"
    # source_pointer = a time window covering the predecessor's WHOLE transcript: the grader
    # derives ground truth from the transcript region a pointer names (there is no stored
    # answer key), and a whole-transcript region always takes its citation-grounding branch,
    # independent of how much the predecessor happened to say.
    import datetime as _dt
    window = f"2000-01-01T00:00:00Z..{(_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    p.write_text(f"""# HANDOFF — {seat} gen{gen} → gen{gen + 1} (synthesized by `orchestra rotate --synthesize`)

You are the successor for canonical seat `{seat}` (runtime {row.get('runtime', 'claude')}). The predecessor,
generation {gen}, ran as session {sid} in tmux pane {pane}; {q3_fact}. Its role prompt is {prompt}. This baton is {p}.
This install serves the gateway on port {st.gateway_port} and the dashboard on port {st.dashboard_port}.
First effect after promotion: `python3 msg_store.py inbox --agent {seat}` (the store is {tasks_db};
the seat registry is {registry}), then continue the seat's standing duties. Never commit or modify the
operator's checkout unless a commission asks for it.

## canary_questions
- {{id: q1, question: "State, one bullet each: the session id the predecessor (generation {gen}) ran under and which generation you are; the ports this install's gateway and dashboard use and where the message store database is; {q3_q[0].lower() + q3_q[1:]}", expected_answer: "Predecessor session {sid}, generation {gen}; I am generation {gen + 1} of seat {seat}. Gateway port {st.gateway_port}, dashboard port {st.dashboard_port}; the store is {tasks_db}. {q3_a}", source_pointer: "jsonl:{window}"}}
""")
    return p


def _predecessor_text_chars(st: S.Settings, row: dict) -> int | None:
    """Characters of user+assistant text in the predecessor's transcript (claude only)."""
    sid, _ = _pane_facts(st, row.get("tmux_session", row.get("name", "")))
    if sid == "unknown":
        return None
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    hits = list(Path(cfg, "projects").glob(f"*/{sid}.jsonl"))
    if not hits:
        return None
    total = 0
    try:
        for line in hits[0].read_text(errors="replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            m = d.get("message") if isinstance(d.get("message"), dict) else {}
            c = m.get("content")
            if isinstance(c, str):
                total += len(c)
            elif isinstance(c, list):
                total += sum(len(b.get("text", "")) for b in c if isinstance(b, dict))
    except OSError:
        return None
    return total


def cmd_rotate(ns) -> int:
    st = S.load_settings()
    if not st.config_exists:
        print(f"no config at {st.config_path} — run `orchestra init` first", file=sys.stderr); return 2
    seat = ns.seat
    _, reg = _registry(st)
    row = reg["agents"].get(seat)
    if not row:
        print(f"seat '{seat}' is not in {st.data_dir / 'registry.json'} — `orchestra spawn {seat}` first", file=sys.stderr); return 2
    handoff = st.data_dir / "docs" / f"HANDOFF_{seat}-next.md"
    if ns.synthesize:
        handoff = _synthesize_handoff(st, seat, row)
        n = _predecessor_text_chars(st, row)
        if n is not None and n < 2000:
            print(f"warning: the predecessor has only {n} characters of conversation so far. The strict grader "
                  f"grounds the successor's answers in the predecessor's transcript and needs a few thousand "
                  f"characters to work with; give the seat some real work first (mail it questions, let it "
                  f"answer) or the rotation will HOLD with 'missed q1..q3'.", file=sys.stderr)
    if not handoff.exists() or "canary_questions" not in handoff.read_text(errors="ignore"):
        print(f"no banked handoff with a canary_questions block at {handoff}.\n"
              f"Ask the seat to bank one (its prompt knows the format), or run `orchestra rotate {seat} --synthesize` "
              f"for a minimal baton (the successor then proves only that it can read its own seat).", file=sys.stderr)
        return 2
    env = S.child_env(st)
    env["AGENT_RUNTIME"] = ns.runtime or row.get("runtime", "claude")
    argv = [sys.executable, str(st.repo_root / "scripts" / "rotate_agent.py"), seat, "--force"]
    if ns.dry_run:
        argv.append("--dry-run")
    if getattr(ns, "resume", False):
        argv.append("--skip-spawn")
    if ns.runtime:
        argv += ["--runtime", ns.runtime]
    if ns.model or row.get("model"):
        argv += ["--model", ns.model or row["model"]]
    print(f"rotate {seat}: baton {handoff}\n  {' '.join(argv)}")
    return _run(argv, env=env, cwd=str(st.repo_root))


# ---- `orchestra starter`: the first-install default team ----
# Shaw, 2026-10-08: "give them a gm, a project manager, and a worker under that project manager.
# That way they have one t0, one t1, and one t2 agent from the start" — "MAKE THAT THE DEFAULT".
# One verb, so a first install is one line (a pasted three-line block is how finding #5 happened).
# Built on the two existing verbs, never a third spawn path: gm via spawn --gm, the PM and worker
# via agent create (template filled, reports_to recorded, ALIVE by effect). Re-runnable: a seat
# that is registered and alive is skipped; one that is registered but dead is relaunched with the
# prompt it already has (agent create refuses to overwrite a prompt, by design).

STARTER_PM_TASK = ("You are the starter project manager, reporting to gm. Tell gm you are ready, "
                   "then park until gm or the operator gives you a project.")
STARTER_DEV_TASK = ("You are the starter worker, reporting to your project manager. Tell it you are "
                    "ready, then park until it gives you work.")


_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def _starter_plan(project: str) -> list[dict]:
    pm, dev = f"pm-{project}", f"dev-{project}"
    return [
        {"name": "gm", "tier": "T0", "gm": True, "template": None, "parent": None, "task": None},
        {"name": pm, "tier": "T1", "gm": False, "template": "pm", "parent": "gm", "task": STARTER_PM_TASK,
         "set": [f"PROJECT={project}", "CLIENT_NAME=none yet", f"CLIENT_SLUG={project}", "BRANCH=main"]},
        {"name": dev, "tier": "T2", "gm": False, "template": "dev", "parent": pm, "task": STARTER_DEV_TASK,
         "set": [f"PROJECT={project}"]},
    ]


def cmd_starter(ns) -> int:
    _apply_verbose(ns)
    st = S.load_settings()
    if not st.config_exists:
        print(f"no config at {st.config_path} — run `orchestra init` first", file=sys.stderr); return 2
    if not _PROJECT_RE.match(ns.project or ""):
        # tmux rewrites "." and refuses some characters, so the seat names must stay plain
        print(f"--project {ns.project!r}: use lowercase letters, digits and dashes only "
              f"(start with a letter or digit, at most 40), e.g. --project website", file=sys.stderr)
        return 2
    refused = _refuse_if_no_runtime_authed(st)
    if refused is not None:
        return refused
    lock = _starter_lock(st)
    if lock is False:
        print("another `orchestra starter` is already running on this install (Arturo may be setting up "
              "the team): wait for it to finish, then run this again", file=sys.stderr)
        return 3
    try:
        return _starter_seats(st, ns)
    finally:
        if lock is not None:
            lock.close()                      # closing the file releases the flock


def _starter_lock(st):
    """One starter run per install: an advisory flock on <data>/state/starter.lock, the file Arturo's
    create_starter_team (#268) holds for its run. Without it an operator who said yes to Arturo and
    then ran `orchestra starter` raced it, two writers on registry.json (pm doc test, 2026-10-08).
    Returns the open file (held), False when another run holds it, or None when the lock is
    already held for us: Arturo runs this CLI as its child with ORCHESTRA_STARTER_LOCK_HELD=1."""
    if os.environ.pop("ORCHESTRA_STARTER_LOCK_HELD", None) == "1":
        # Popped, not just read: child_env copies os.environ, and if this run starts the tmux
        # server, the server keeps the flag and every later seat would skip the lock.
        return None
    import fcntl
    path = st.data_dir / "state" / "starter.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    return f


def _starter_seats(st, ns) -> int:
    import argparse
    for seat in _starter_plan(ns.project):
        name = seat["name"]
        p, reg = _registry(st)
        row = reg["agents"].get(name)
        # register_seat keeps an existing row verbatim, so a gm written as T2 (spawned without
        # --gm) stayed T2 forever. The starter team owns these three tiers: correct them.
        if row and (row.get("tier") != seat["tier"] or (seat["gm"] and row.get("always_on") is not True)):
            print(f"{name} tier {row.get('tier')} -> {seat['tier']}")
            row["tier"] = seat["tier"]
            if seat["gm"]:
                row["always_on"] = True
            reg["agents"][name] = row
            S.guard_test_write(p, "registry.json"); tmp = p.with_suffix(".json.tmp"); tmp.write_text(json.dumps(reg, indent=2) + "\n"); os.replace(tmp, p)
        if row and _pane_alive(row.get("tmux_session") or name):
            print(f"{name} ({seat['tier']}) already running — skipped")
            continue
        if seat["gm"] or row or (st.repo_root / f"prompts/{name}.md").exists():
            # gm, or a registered/prompted seat whose CLI exited: relaunch, keep its prompt
            rc = cmd_spawn(argparse.Namespace(seat=name, gm=seat["gm"], task=seat["task"], runtime=ns.runtime,
                                              model=None, tier=seat["tier"], prompt=None))
            if rc == 0 and seat["parent"]:
                p, reg = _registry(st)
                if reg["agents"][name].get("reports_to") != seat["parent"]:
                    reg["agents"][name]["reports_to"] = seat["parent"]
                    S.guard_test_write(p, "registry.json"); tmp = p.with_suffix(".json.tmp"); tmp.write_text(json.dumps(reg, indent=2) + "\n"); os.replace(tmp, p)
        else:
            cwd = None
            if seat["tier"] == "T2":
                # the first worker works in its own project folder, not inside the harness checkout
                proj = st.data_dir / "projects" / ns.project
                proj.mkdir(parents=True, exist_ok=True)
                cwd = str(proj)
            rc = cmd_agent_create(argparse.Namespace(name=name, tier=seat["tier"], runtime=ns.runtime, model=None,
                                                     parent=seat["parent"], template=seat["template"],
                                                     set=seat["set"], task=seat["task"], cwd=cwd))
        if rc != 0:
            print(f"starter stopped at {name}: fix the error above, then run `orchestra starter` again "
                  f"(seats already running are skipped)", file=sys.stderr)
            return rc
        if not _pane_alive(name):
            print(f"starter stopped: {name} is not alive by effect; check {st.data_dir / 'logs'}", file=sys.stderr)
            return 1
    pm, dev = f"pm-{ns.project}", f"dev-{ns.project}"
    print(f"\nstarter team up: gm (T0) -> {pm} (T1) -> {dev} (T2)")
    print("talk to gm:   tmux attach -t gm      (detach: Ctrl-B then D)")
    return 0
