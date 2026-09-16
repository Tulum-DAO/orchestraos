"""p8 reboot-recovery hook (RED) — agent-recovery.sh --boot starts the regenerator.

The cutover flag survives a reboot but the daemon does not, so the canonical @reboot
entry (`agent-recovery.sh --boot`, crontab `@reboot sleep 45 && ... --boot`) must
(re)start the regenerator when the cutover is active. gm-ruled location.

This test runs the ACTUAL hook bytes extracted from agent-recovery.sh (between the
marker comments — zero drift) in a controlled harness, triangulating the contract:
  * boot + armed          -> start_if_armed spawns the daemon (lock appears)
  * boot + flag-off       -> INERT no-op (no daemon, clean exit)  [byte-identical boot]
  * NON-boot + armed      -> the BOOT_MODE guard blocks it (no daemon)  [--cron safe]

RED until the hook (with markers) exists in scripts/agent-recovery.sh.
"""
import json
import os
import subprocess
import sys
import time

import pytest

from scripts.identity_store import migrate, orchestra_db

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RECOVERY_SH = os.path.join(REPO, "scripts", "agent-recovery.sh")
_HOOK_BEGIN = "# >>> identity-store regenerator boot hook"
_HOOK_END = "# <<< identity-store regenerator boot hook"


def _extract_hook():
    txt = open(RECOVERY_SH).read()
    if _HOOK_BEGIN not in txt or _HOOK_END not in txt:
        return ""
    return txt.split(_HOOK_BEGIN, 1)[1].split(_HOOK_END, 1)[0]


@pytest.fixture
def sandbox(tmp_path):
    (tmp_path / "state" / "agents").mkdir(parents=True)
    (tmp_path / "registry.json").write_text(json.dumps(
        {"agents": {"a1": {"name": "a1", "tier": "T2", "machine": "vps", "cwd": "/x",
                           "runtime": "claude", "model": "m", "tmux_session": "a1",
                           "always_on": True, "system_prompt": "p", "status": "online",
                           "generation": 1, "session_id": "s1", "lineage_root": "a1"}},
         "_retired_agents": {}, "_provisional": {}, "_canonical": {"a1": "x"}}))
    (tmp_path / "state" / "agent-sessions.json").write_text(json.dumps(
        {"a1": {"session_id": "s1", "model": "m", "generation": 1, "status": "online",
                "tmux_session": "a1"}}))
    (tmp_path / "state" / "agents" / "a1.json").write_text(json.dumps(
        {"agent_id": "a1", "status": "online"}))
    dbp = tmp_path / "state" / "orchestra-registry.db"
    orchestra_db.init_db(str(dbp))
    c = orchestra_db.get_connection(str(dbp))
    try:
        migrate.migrate(c, registry_path=str(tmp_path / "registry.json"),
                        sessions_path=str(tmp_path / "state" / "agent-sessions.json"),
                        agents_dir=str(tmp_path / "state" / "agents"))
    finally:
        c.close()
    return tmp_path


def _run_hook(sandbox, *, boot: bool):
    hook = _extract_hook()
    # Reproduce the real script's environment the hook depends on (SCRIPT_DIR/LOG/
    # BOOT_MODE/log()) so the extracted hook bytes run exactly as they would in situ.
    harness = (
        'set -uo pipefail\n'
        f'SCRIPT_DIR="{sandbox}"\n'
        f'LOG="{sandbox}/hook.log"\n'
        f'BOOT_MODE={"true" if boot else "false"}\n'
        'log() { echo "$*" >> "$LOG"; }\n'
        f'{hook}\n'
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO
    return subprocess.run(["bash", "-c", harness], capture_output=True, text=True,
                          env=env, cwd=REPO, timeout=30)


def _lock(sandbox):
    return sandbox / "state" / "identity-store-regenerator.lock"


def _kill_daemon(sandbox):
    lk = _lock(sandbox)
    if lk.exists():
        try:
            os.kill(int(lk.read_text()), 15)
        except (ProcessLookupError, ValueError):
            pass


def test_boot_armed_starts_daemon(sandbox):
    from scripts.identity_store import cutover
    cutover.arm(str(sandbox))
    try:
        r = _run_hook(sandbox, boot=True)
        assert r.returncode == 0, r.stderr
        # the spawned daemon acquires the singleton lock shortly after fork
        appeared = False
        for _ in range(40):
            if _lock(sandbox).exists():
                appeared = True
                break
            time.sleep(0.1)
        assert appeared, "boot+armed: the regenerator daemon must be started (lock)"
    finally:
        _kill_daemon(sandbox)


def test_boot_flagoff_is_inert(sandbox):
    # no cutover flag armed
    r = _run_hook(sandbox, boot=True)
    assert r.returncode == 0, r.stderr
    time.sleep(0.5)
    assert not _lock(sandbox).exists(), "boot+flag-off: no daemon (INERT boot)"


def test_nonboot_armed_guard_blocks(sandbox):
    from scripts.identity_store import cutover
    cutover.arm(str(sandbox))
    try:
        r = _run_hook(sandbox, boot=False)
        assert r.returncode == 0, r.stderr
        time.sleep(0.5)
        assert not _lock(sandbox).exists(), \
            "non-boot: the BOOT_MODE guard must block the daemon (--cron/manual safe)"
    finally:
        _kill_daemon(sandbox)
