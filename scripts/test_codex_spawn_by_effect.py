"""BY EFFECT: the real codex launch line, as spawn_agent sends it to the pane.

A fake `tmux` logs every call; the line arrives on `send-keys -t <seat> "<line>" Enter`. Asserts
(orchestraos-builder, #430): the data-dir prefix (ORCHESTRA_DIR=...), the BG green marker env,
the installer-session scrub (`env -u ...`), the bypass flags incl. check_for_update_on_startup=false,
and, for a resume, that the line ENDS in `resume <uuid>`; for a fresh spawn, no `resume` at all.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SID = "00000000-0000-7000-8000-00000000000c"


def _spawn(tmp_path, resume_sid=""):
    tree = tmp_path / "tree"
    (tree / "state").mkdir(parents=True, exist_ok=True)
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text(json.dumps({"tokens": {"access_token": "x"}}))
    log = tmp_path / "tmux.log"
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    (fakebin / "tmux").write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> "{log}"\n'
        'case "$1" in capture-pane) printf "\\u203a Ask Codex to do anything\\n";; has-session) exit 1;; esac\n'
        "exit 0\n")
    (fakebin / "tmux").chmod(0o755)
    (fakebin / "codex").write_text("#!/bin/bash\necho codex-cli 0.153.4\n")
    (fakebin / "codex").chmod(0o755)
    reg = tree / "registry.json"
    reg.write_text(json.dumps({"agents": {"zz-codex": {"name": "zz-codex", "runtime": "codex",
                                                        "model": "gpt-5.6-terra", "tmux_session": "zz-codex", "cwd": str(tree),
                                                        "tier": "T2", "machine": "vps"}}}))
    env = dict(os.environ, ORCHESTRA_DIR=str(tree), REGISTRY=str(reg), NTFY_TOKEN_FILE="/nonexistent",
               HOME=str(home), AGENT_RUNTIME="codex", PATH=f"{fakebin}:{os.environ.get('PATH', '')}",
               BG_GREEN_ROOT="zz-codex", BG_GREEN_ALIAS="zz-codex-g2",
               BG_QUARANTINE_ALIAS="zz-codex-g2", BG_QUARANTINE_WALDIR=str(tree / "state" / "wal"))
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    script = (f'sleep() {{ :; }}; unset IDENTITY_STORE_CUTOVER; '
              f'spawn_agent zz-codex "do nothing" "{resume_sid}"; echo "rc=$?"')
    r = subprocess.run(["bash", "-c", f'source "$1"; {script}', "spawn-test", str(ROOT / "spawn-agent.sh")],
                       capture_output=True, text=True, cwd=ROOT, env=env, timeout=120)
    calls = log.read_text().splitlines() if log.exists() else []
    lines = [c for c in calls if c.startswith("send-keys -t zz-codex ") and "codex" in c and "--yolo" in c]
    return r, lines, calls


@pytest.mark.parametrize("resume", [SID, ""])
def test_the_line_the_pane_receives_keeps_every_prefix_and_ends_in_resume_sid(tmp_path, resume):
    r, lines, calls = _spawn(tmp_path, resume)
    assert lines, (r.stdout[-1500:], r.stderr[-1500:], calls[-10:])
    line = lines[-1]
    assert f"ORCHESTRA_DIR={tmp_path / 'tree'}" in line, line
    assert "BG_GREEN_ROOT=zz-codex" in line and "BG_QUARANTINE_ALIAS=zz-codex-g2" in line, line
    assert "env -u " in line, line
    assert "--yolo --dangerously-bypass-hook-trust -c check_for_update_on_startup=false" in line, line
    assert "--model gpt-5.6-terra" in line, line
    if resume:
        assert line.rstrip().endswith(f"resume {SID} Enter"), line
    else:
        assert " resume" not in line, line
    # order: prefixes BEFORE the binary; the tail AFTER the flags
    assert line.index("ORCHESTRA_DIR=") < line.index("/codex --yolo") < line.index("check_for_update_on_startup=false")
