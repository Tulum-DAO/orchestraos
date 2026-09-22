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

DEFAULT_MODEL = {"claude": "claude-opus-5[1m]", "gemini": "gemini-3.7-flash", "codex": "gpt-5.6-terra"}


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


def register_seat(st: S.Settings, seat: str, *, gm: bool, runtime: str | None, model: str | None,
                  tier: str | None, prompt: str | None) -> dict:
    """Idempotent: an existing row is kept verbatim (only missing fields are filled)."""
    p, reg = _registry(st)
    row = dict(reg["agents"].get(seat) or {})
    runtime = runtime or row.get("runtime") or (st.runtimes_enabled or ["claude"])[0]
    default_prompt = "prompts/gm.md" if gm else f"prompts/{seat}.md"
    row.setdefault("name", seat)
    row.setdefault("runtime", runtime)
    if model:
        row["model"] = model
    row.setdefault("model", DEFAULT_MODEL.get(row["runtime"], ""))
    # T0 = the always-on manager seat, T1 = coordinators, T2 = workers (docs/REFERENCE_INSTALL.md).
    row.setdefault("tier", tier or ("T0" if gm else "T2"))
    row.setdefault("machine", "local")
    row.setdefault("cwd", str(st.repo_root))
    row.setdefault("tmux_session", seat)
    row.setdefault("system_prompt", prompt or default_prompt)
    row.setdefault("always_on", bool(gm))
    row.setdefault("generation", 1)
    row.setdefault("status", "provisioning")
    reg["agents"][seat] = row
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp"); tmp.write_text(json.dumps(reg, indent=2) + "\n"); os.replace(tmp, p)
    return row


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


def cmd_spawn(ns) -> int:
    st = S.load_settings()
    if not st.config_exists:
        print(f"no config at {st.config_path} — run `orchestra init` first", file=sys.stderr); return 2
    refused = _refuse_if_no_runtime_authed(st)
    if refused is not None:
        return refused
    seat = ns.seat
    row = register_seat(st, seat, gm=ns.gm, runtime=ns.runtime, model=ns.model, tier=ns.tier, prompt=ns.prompt)
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
    print(f"talk to it:   tmux attach -t {row['tmux_session']}\nmail it:      python3 msg_store.py send --from you --to {seat} --subject hi --body-file note.txt")
    return 0


def _pane_alive(tmux_session: str) -> bool:
    """ALIVE = the session exists AND its pane runs a child process (the agent CLI), not a bare
    shell left behind by a CLI that exited (issue #93: two 'spawned successfully' seats were dead)."""
    if not _tmux_has_session(tmux_session):
        return False
    try:
        out = subprocess.run(["tmux", "list-panes", "-t", f"={tmux_session}", "-F", "#{pane_pid}"],
                             capture_output=True, text=True, timeout=5)
        pid = (out.stdout.split() or [""])[0]
        if not pid:
            return False
        kids = subprocess.run(["pgrep", "-P", pid], capture_output=True, text=True, timeout=5)
        return bool(kids.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


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
    runtime = ns.runtime or (st.runtimes_enabled or ["claude"])[0]
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
        values = {"DEV_NAME": name, "PM_NAME": name, "YOUR_ID": name, "CWD": str(st.repo_root)}
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
    if refused is not None:
        return refused
    row = register_seat(st, name, gm=False, runtime=runtime, model=ns.model, tier=ns.tier, prompt=prompt_rel)
    if ns.parent and row.get("reports_to") != ns.parent:
        p, reg = _registry(st); reg["agents"][name]["reports_to"] = ns.parent; row["reports_to"] = ns.parent
        tmp = p.with_suffix(".json.tmp"); tmp.write_text(json.dumps(reg, indent=2) + "\n"); os.replace(tmp, p)
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
