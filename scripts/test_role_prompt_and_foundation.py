"""Every seat is handed its role prompt, and the public foundation layer ships (operator
finding #6, 2026-10-08).

Until this fix spawn-agent.sh assembled FOUNDATION_STATIC.md + the role prompt into
/tmp/agent-prompt-<id>.md and then never referenced it: the init file a seat is told to read
did not mention it, so every seat ran without its role prompt unless its task text happened to
say "read your prompt". Public main also had no FOUNDATION_STATIC.md at all, so every spawn
warned about it.

The end-to-end test runs the REAL spawn-agent.sh against a sandbox data dir, a private tmux
server (a PATH shim pins -S) and a stub `claude` that only sleeps, so no CLI, login or plan is
used, and reads the init file it writes.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FOUNDATION = ROOT / "prompts" / "FOUNDATION_STATIC.md"


def _real_tmux():
    for d in os.environ.get("PATH", "").split(os.pathsep):
        cand = Path(d) / "tmux"
        if cand.is_file() and os.access(cand, os.X_OK) and cand.read_bytes()[:2] != b"#!":
            return str(cand)
    return None


@pytest.fixture()
def sandbox_spawn():
    real = _real_tmux()
    if real is None:
        pytest.skip("tmux binary not found")
    d = Path(tempfile.mkdtemp(prefix="sp", dir="/tmp"))      # short: unix socket path limit
    seat = f"probe-{uuid.uuid4().hex[:8]}"
    (d / "bin").mkdir()
    (d / "data" / "state").mkdir(parents=True)
    (d / "bin" / "tmux").write_text(f'#!/bin/sh\nexec "{real}" -S "{d}/s" "$@"\n')
    # Prints the claude ready-signature (❯, spawn-agent.sh wait_for_tui) so the spawn does not
    # sit out its 30 s TUI wait; then idles like a real seat.
    (d / "bin" / "claude").write_text("#!/bin/sh\nprintf '\\342\\235\\257 \\n'\nsleep 300\n")
    for f in (d / "bin").iterdir():
        f.chmod(0o755)
    (d / "data" / "registry.json").write_text(json.dumps({"version": 1, "agents": {seat: {
        "name": seat, "tmux_session": seat, "tier": "T2", "runtime": "claude", "machine": "vps",
        "cwd": str(d), "system_prompt": "prompts/gm.md"}}}))
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "ORCHESTRA_SPAWN_VERBOSE")}
    env.update(PATH=f"{d / 'bin'}{os.pathsep}{env.get('PATH', '')}", ORCHESTRA_DIR=str(d / "data"),
               ORCH_DIR=str(d / "data"), AGENT_RUNTIME="claude")
    assert shutil.which("tmux", path=env["PATH"]) == str(d / "bin" / "tmux")     # FENCE

    def spawn(*extra):
        return subprocess.run(["bash", str(ROOT / "spawn-agent.sh"), seat, *extra], env=env,
                              cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    try:
        yield seat, spawn
    finally:
        subprocess.run([real, "-S", f"{d}/s", "kill-server"], capture_output=True, timeout=10)
        shutil.rmtree(d, ignore_errors=True)
        for p in (f"/tmp/agent-init-{seat}.md", f"/tmp/agent-prompt-{seat}.md"):
            Path(p).unlink(missing_ok=True)


def test_the_seat_is_told_to_read_its_role_prompt_first(sandbox_spawn):
    seat, spawn = sandbox_spawn
    out = spawn()
    init = Path(f"/tmp/agent-init-{seat}.md").read_text()
    combined = Path(f"/tmp/agent-prompt-{seat}.md")
    assert f"FIRST, read {combined}" in init, out.stdout + out.stderr
    text = combined.read_text()
    # the foundation first, then the seat's own role prompt (here prompts/gm.md)
    assert text.startswith(FOUNDATION.read_text().splitlines()[0])
    assert "# --- ROLE-SPECIFIC PROMPT ---" in text
    assert (ROOT / "prompts" / "gm.md").read_text().strip().splitlines()[0] in text
    assert "FOUNDATION_STATIC.md not found" not in out.stdout + out.stderr


def test_a_default_spawn_is_quiet_and_verbose_shows_the_detail(sandbox_spawn):
    seat, spawn = sandbox_spawn
    quiet = spawn()
    noisy_markers = ("[1m]-verify: could not read", "cannot be confirmed from this banner", "Perms: project-local")
    assert not any(m in quiet.stdout + quiet.stderr for m in noisy_markers), quiet.stdout
    # vlog itself, by effect: silent by default, printed with ORCHESTRA_SPAWN_VERBOSE=1
    vlog_def = next(l for l in (ROOT / "spawn-agent.sh").read_text().splitlines() if l.startswith("vlog()"))
    run = lambda env: subprocess.run(["bash", "-c", f"set -euo pipefail; CYAN=; NC=; {vlog_def}; vlog hello; echo rc=$?"],
                                     env=env, capture_output=True, text=True, timeout=10).stdout
    base = {k: v for k, v in os.environ.items() if k != "ORCHESTRA_SPAWN_VERBOSE"}
    assert run(base).split() == ["rc=0"]
    assert "hello" in run({**base, "ORCHESTRA_SPAWN_VERBOSE": "1"})


def test_the_benign_spawn_notes_go_through_vlog():
    src = (ROOT / "spawn-agent.sh").read_text()
    for marker in ("cannot be confirmed from this banner", "could not read a model from", "Perms: project-local allow-rules"):
        line = next(l for l in src.splitlines() if marker in l)
        assert line.strip().split()[0] in ("vlog",) or "vlog \"" in line, line
    assert re.search(r"--verbose\)\s*export ORCHESTRA_SPAWN_VERBOSE=1", src)


# ---- the public foundation: generic, and every command it cites is real ------------------

def test_the_foundation_ships_and_names_no_operator():
    assert FOUNDATION.is_file()
    out = subprocess.run(["python3", str(ROOT / "scripts" / "scan_operator_identifiers.py"),
                          "--baseline", str(ROOT / "scripts" / "operator_identifiers_baseline.json"),
                          str(FOUNDATION)], capture_output=True, text=True, timeout=60)
    assert "0 NEW" in out.stdout + out.stderr, out.stdout + out.stderr


def test_every_command_the_foundation_cites_exists():
    text = FOUNDATION.read_text()
    for rel in re.findall(r"\$ORCHESTRA_ROOT/([\w./-]+\.py)", text):
        assert (ROOT / rel).is_file(), rel
    store = (ROOT / "msg_store.py").read_text()
    for sub in re.findall(r"msg_store\.py (\w+)", text):
        assert re.search(rf'add_parser\("{sub}"', store), f"msg_store has no '{sub}' command"
    for flag in set(re.findall(r"(--[a-z][a-z-]+)", text)) - {"--worker-kind", "--summary"}:
        assert f'"{flag}"' in store, f"msg_store.py has no {flag}"
    approval = (ROOT / "scripts" / "approval.py").read_text()
    for flag in ("--from", "--worker-kind", "--summary"):
        assert f'"{flag}"' in approval or f"'{flag}'" in approval, flag


def test_the_hackathon_examples_left_prompts():
    for name in ("pm-bliss.md", "second-brain-dev.md", "telegram-helper.md"):
        assert not (ROOT / "prompts" / name).exists()
        assert (ROOT / "examples" / "prompts" / name).is_file()
