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
    row.setdefault("tier", tier or ("T1" if gm else "T2"))
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


def cmd_spawn(ns) -> int:
    st = S.load_settings()
    if not st.config_exists:
        print(f"no config at {st.config_path} — run `orchestra init` first", file=sys.stderr); return 2
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
    """A minimal baton whose canary questions each carry a DISTINCT seat-specific literal: the
    strict grader counts id-class tokens (digits/hex) as anchors and requires every question to
    have at least one anchor no other question shares — session id (q1), the two ports (q2),
    the predecessor's tmux pane id (q3)."""
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
- {{id: q1, question: "Which session id did the predecessor (generation {gen}) run under, and which generation are you?", expected_answer: "Predecessor session {sid}, generation {gen}; I am generation {gen + 1} of seat {seat}.", source_pointer: "jsonl:{window}"}}
- {{id: q2, question: "Which ports do this install's gateway and dashboard use, and where is the message store database?", expected_answer: "Gateway port {st.gateway_port}, dashboard port {st.dashboard_port}; the store is {tasks_db} (read with python3 msg_store.py inbox --agent {seat}).", source_pointer: "jsonl:{window}"}}
- {{id: q3, question: "{q3_q}", expected_answer: "{q3_a}", source_pointer: "jsonl:{window}"}}
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
