"""Every test here runs in a sandbox that cannot touch the developer's live machine:
- CLAUDE_CONFIG_DIR -> tmp: `orchestra init` installs hooks into <config dir>/settings.json
  (2026-09-17: two runs without this fence installed rows into the live file and blocked
  every tool call on the host);
- an isolated tmux server (TMUX unset, TMUX_TMPDIR -> tmp): any spawn/rotate a test performs
  lands on a server the fleet's router, orphan scan and identity store never see, and the
  fixture kills that server on teardown.
- a `tailscale` on PATH that answers nothing: `orchestra init` reads `tailscale status --json` to write
  state/tailscale-users, and a test must never read (or write into a fixture) the developer's own login.
  A test that wants an answer puts its own fake `tailscale` earlier on PATH.
scripts/test_sandbox_env.sh exports the same sandbox for manual by-effect proofs."""
import os
import subprocess

import pytest


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    sock_dir = tmp_path / "tmux"
    sock_dir.mkdir()
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TMUX_TMPDIR", str(sock_dir))
    no_ts = tmp_path / "no-tailscale-bin"
    no_ts.mkdir()
    (no_ts / "tailscale").write_text("#!/bin/sh\nexit 1\n")
    (no_ts / "tailscale").chmod(0o755)
    monkeypatch.setenv("PATH", f"{no_ts}{os.pathsep}{os.environ.get('PATH', '')}")
    yield
    env = dict(os.environ, TMUX_TMPDIR=str(sock_dir))
    env.pop("TMUX", None)
    # kill-server is forbidden on shared hosts (a wrapper refuses it); kill each session instead
    ls = subprocess.run(["tmux", "ls", "-F", "#S"], env=env, capture_output=True, text=True)
    for name in ls.stdout.split():
        subprocess.run(["tmux", "kill-session", "-t", name], env=env, capture_output=True)
