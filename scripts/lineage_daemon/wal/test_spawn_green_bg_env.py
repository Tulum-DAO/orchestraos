"""RED (BG leg-(ii) P2.7, Seam 1 — green-init env propagation).

spawn_green today runs `spawn-agent.sh <alias>` with NO BG env (spawn_green.py:107
runner(green_alias)), so spawn-agent.sh cannot tell it is spawning a BG green: the
green never gets BG_GREEN_ROOT/ALIAS (green_boot_probe + capture_green_sid stay
INERT) or the BG_QUARANTINE_ALIAS/WALDIR that M2 needs. P2.7 makes spawn_green build
the green's spawn env and pass it THROUGH the runner to the subprocess.

Drives the REAL spawn_green() (leg-(i) lesson: exercise the live path, not a mock) via
an injected capturing runner + injected pane-pid fn — so no tmux is needed, yet the
whole spawn_green body runs and must construct + hand off the env.

RED until spawn_green builds bg_green_env and passes it to the runner.
"""
import os

from scripts.lineage_daemon.wal import spawn_green

ROOT = "second-brain-dev"
ALIAS = "second-brain-dev-g5"


def test_spawn_green_passes_bg_envs_to_runner(tmp_path):
    wal = str(tmp_path / "wal")
    os.makedirs(wal)
    captured = {}

    def capturing_runner(session, env=None):
        captured["session"] = session
        captured["env"] = env
        return {"session": session}

    spawn_green.spawn_green(
        ROOT, ALIAS, orchestra_dir=str(tmp_path), wal_dir=wal,
        spawn_runner=capturing_runner,
        has_session_fn=lambda s: False,   # force a fresh spawn (runner is invoked)
        pane_pid_fn=lambda s: 4242,       # a reachable pid so spawn_green succeeds
        pane_id_fn=lambda s: "%9",
    )

    env = captured.get("env")
    assert env is not None, "the runner must receive the green's spawn env (was None)"
    assert env.get("BG_GREEN_ROOT") == ROOT
    assert env.get("BG_GREEN_ALIAS") == ALIAS
    # quarantine envs ride the same green spawn (M2 contract; bg_quarantine.py:30-31)
    assert env.get("BG_QUARANTINE_ALIAS") == ALIAS
    assert env.get("BG_QUARANTINE_WALDIR") == os.path.abspath(wal)
    assert os.path.isabs(env["BG_QUARANTINE_WALDIR"]), "WALDIR must be absolute (green cwd != orch dir)"
    # env AUGMENTS the parent environment, never replaces it (PATH etc. survive)
    assert "PATH" in env


def test_bg_green_env_is_pure_and_complete(tmp_path):
    wal = str(tmp_path / "wal")
    env = spawn_green.bg_green_env(ROOT, ALIAS, wal)
    assert env == {
        "BG_GREEN_ROOT": ROOT,
        "BG_GREEN_ALIAS": ALIAS,
        "BG_QUARANTINE_ALIAS": ALIAS,
        "BG_QUARANTINE_WALDIR": os.path.abspath(wal),
    }
