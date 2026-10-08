"""Every seat's tmux session gets mouse on, and a detach hook that keeps injection safe.

Operator finding, 2026-10-08: with mouse mode off the terminal turns the scroll wheel into
arrow keys; in Claude Code Up walks the prompt history shared by every seat in the same
directory, so a scroll and an Enter re-sent ANOTHER seat's init prompt.

Mouse on has a cost (independent review of the first version): a scroll-up leaves the pane in
tmux's scroll mode, and `send-keys` text is then swallowed with no error. The client-detached
hook leaves that mode, so an unattended pane is never stuck in it.

These tests run a REAL tmux. Every call, including the ones the helper makes, goes through a
`tmux` shim first on PATH that pins `-S <private socket>`, and TMUX is removed from the
environment, so nothing here can reach a live tmux server.
"""
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "tmux_session_defaults.sh"


def _real_tmux():
    for d in os.environ.get("PATH", "").split(os.pathsep):
        cand = Path(d) / "tmux"
        # skip wrapper scripts (this repo's tmux-guard style shims); we want the binary
        if cand.is_file() and os.access(cand, os.X_OK) and not cand.read_bytes()[:2] == b"#!":
            return str(cand)
    return None


REAL = _real_tmux()
pytestmark = pytest.mark.skipif(REAL is None, reason="tmux binary not found")


@pytest.fixture()
def private_tmux():
    # A SHORT dir: a unix socket path over ~104 bytes fails (pytest's tmp_path can be longer).
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="tmx", dir="/tmp"))
    sock = d / "s"
    bindir = d / "bin"
    bindir.mkdir()
    shim = bindir / "tmux"
    shim.write_text(f'#!/bin/sh\nexec "{REAL}" -S "{sock}" "$@"\n')
    shim.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"

    def run(*args, check=True):
        return subprocess.run(["tmux", *args], env=env, capture_output=True, text=True,
                              check=check, timeout=10)

    # FENCE, before any tmux call: the tmux on PATH is our shim.
    assert shutil.which("tmux", path=env["PATH"]) == str(shim)
    run("-f", "/dev/null", "new-session", "-d", "-s", "seat-x", "-x", "80", "-y", "20",
        "seq 1 200; sleep 600")
    try:
        yield env, run
    finally:
        subprocess.run([REAL, "-S", str(sock), "kill-server"], capture_output=True, timeout=10)
        shutil.rmtree(d, ignore_errors=True)


def _helper(env, session):
    return subprocess.run(["bash", "-c", f"set -euo pipefail; source '{HELPER}'; "
                           f"orch_tmux_session_defaults \"$1\"; echo rc=$?", "_", session],
                          env=env, capture_output=True, text=True, timeout=10)


def _opt(run, session, name):
    return run("show-options", "-t", f"={session}:", "-v", name).stdout.strip()


def test_the_seat_session_gets_mouse_on_and_nothing_else_does(private_tmux):
    env, run = private_tmux
    run("new-session", "-d", "-s", "seat-xy", "sleep 600")     # a prefix-sharing neighbour
    assert _opt(run, "seat-x", "mouse") in ("", "off")
    out = _helper(env, "seat-x")
    assert out.stdout.strip() == "rc=0", out.stderr
    assert _opt(run, "seat-x", "mouse") == "on"
    assert run("show-options", "-g", "-v", "mouse").stdout.strip() == "off"
    assert _opt(run, "seat-xy", "mouse") in ("", "off")


def test_a_gone_session_never_prefix_matches_a_neighbour(private_tmux):
    """seat-x died and seat-xy lives: the defaults for seat-x must not land on seat-xy."""
    env, run = private_tmux
    run("new-session", "-d", "-s", "seat-xy", "sleep 600")
    run("kill-session", "-t", "=seat-x")
    assert _helper(env, "seat-x").stdout.strip() == "rc=0"
    assert _opt(run, "seat-xy", "mouse") in ("", "off")
    assert "client-detached" not in run("show-hooks", "-t", "=seat-xy:").stdout


def test_a_missing_or_empty_session_never_fails_the_caller(private_tmux):
    """Fail-soft under set -euo pipefail: spawn and recovery must not abort on it."""
    env, _ = private_tmux
    assert _helper(env, "no-such-seat").stdout.strip() == "rc=0"
    assert _helper(env, "").stdout.strip() == "rc=0"
    no_arg = subprocess.run(["bash", "-c", f"set -euo pipefail; source '{HELPER}'; "
                             "orch_tmux_session_defaults; echo rc=$?"],
                            env=env, capture_output=True, text=True, timeout=10)
    assert no_arg.stdout.strip() == "rc=0", no_arg.stderr


@pytest.mark.skipif(shutil.which("script") is None, reason="util-linux script(1) not installed")
@pytest.mark.parametrize("with_defaults", [False, True])
def test_a_pane_left_scrolled_up_by_a_detached_client_still_takes_injected_text(private_tmux,
                                                                                with_defaults):
    """The review's scenario: the operator scrolls up in a seat, then detaches; later an
    injector sends text + Enter. Without the hook the pane stays in scroll mode and the text is
    lost (the control proves the test can see the failure); with it the text arrives."""
    env, run = private_tmux
    run("set-option", "-t", "=seat-x:", "mouse", "on")
    if with_defaults:
        assert _helper(env, "seat-x").stdout.strip() == "rc=0"
    # CI runners have no TERM, and tmux attach then fails ("open terminal failed").
    client = subprocess.Popen(["script", "-qc", "tmux attach -t =seat-x", "/dev/null"],
                              env={**env, "TERM": "xterm-256color"}, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if run("list-clients", check=False).stdout.strip():
                break
            time.sleep(0.1)
        assert run("list-clients").stdout.strip(), "the test client never attached"
        run("copy-mode", "-t", "=seat-x:")                     # what a wheel scroll-up does
        assert run("display", "-p", "-t", "=seat-x:", "#{pane_in_mode}").stdout.strip() == "1"
        run("detach-client", "-s", "=seat-x")
        time.sleep(0.5)
        in_mode = run("display", "-p", "-t", "=seat-x:", "#{pane_in_mode}").stdout.strip()
        # check=False: in scroll mode with no client, tmux answers "no current client" (rc 1)
        # and drops the text. Injectors that ignore the rc lose the message silently.
        run("send-keys", "-t", "=seat-x:", "inject-marker", "Enter", check=False)
        time.sleep(0.3)
        seen = "inject-marker" in run("capture-pane", "-p", "-t", "=seat-x:").stdout
    finally:
        client.kill()
    if with_defaults:
        assert in_mode == "0" and seen
    else:
        assert in_mode == "1" and not seen


def test_a_name_tmux_could_misparse_gets_mouse_but_no_hook(private_tmux):
    env, run = private_tmux
    run("new-session", "-d", "-s", "it's", "sleep 600")
    assert _helper(env, "it's").stdout.strip() == "rc=0"
    assert _opt(run, "it's", "mouse") == "on"
    assert "client-detached" not in run("show-hooks", "-t", "=it's:").stdout


def test_every_seat_session_site_applies_the_defaults():
    """Derived from the code, not a list: every `tmux new-session` that creates a seat in the
    two seat launchers is followed (within 6 lines) by orch_tmux_session_defaults, and each
    launcher sources the helper."""
    for rel in ("spawn-agent.sh", "scripts/agent-recovery.sh"):
        lines = (ROOT / rel).read_text().splitlines()
        assert any("tmux_session_defaults.sh" in l and l.lstrip().startswith("source") for l in lines), rel
        sites = [i for i, l in enumerate(lines) if re.match(r"\s*tmux new-session\b", l)]
        assert sites, f"{rel}: no new-session found; did the launcher move?"
        for i in sites:
            window = "\n".join(lines[i + 1:i + 7])
            assert 'orch_tmux_session_defaults "$tmux_name"' in window, f"{rel}:{i + 1}"


def _clients(run):
    return set(run("list-clients", "-F", "#{client_tty}", check=False).stdout.split())


def _wait(pred, secs=5.0):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return pred()


def test_a_client_attached_from_inside_a_seat_pane_is_detached(private_tmux):
    """Operator finding #10, 2026-10-08: gm's pane ran a nested `tmux attach` (list-panes:
    "gm tmux"), so its CLI was off screen, the detector found none, and the dashboard showed
    a dead gm with a Spawn button. A client whose tty is one of this server's own panes is
    a seat attaching to a seat: the seat's client-attached hook detaches it at once. A client
    from outside (the operator's ssh terminal, the web terminal) is left alone."""
    env, run = private_tmux
    assert _helper(env, "seat-x").stdout.strip() == "rc=0"
    outer = None
    if shutil.which("script"):
        outer = subprocess.Popen(["script", "-qfc", "tmux attach -t =seat-x:", "/dev/null"],
                                 env={**env, "TERM": "xterm"}, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert _wait(lambda: len(_clients(run)) == 1), "the outer client never attached"
    try:
        run("new-session", "-d", "-s", "seat-n", "-x", "80", "-y", "20",
            "env -u TMUX tmux attach -t =seat-x:; sleep 600")
        inner_tty = run("display-message", "-p", "-t", "=seat-n:", "#{pane_tty}").stdout.strip()
        assert _wait(lambda: run("display-message", "-p", "-t", "=seat-n:",
                                 "#{pane_current_command}").stdout.strip() == "sleep"), \
            "the nested client was never detached"
        assert inner_tty not in _clients(run)
        if outer is not None:
            assert len(_clients(run)) == 1, "the outside client must stay attached"
    finally:
        if outer is not None:
            outer.kill()


def test_the_attach_guard_is_per_seat_not_global(private_tmux):
    env, run = private_tmux
    _helper(env, "seat-x")
    # show-hooks lists every hook NAME, set or not; a set hook prints as name[index].
    assert "client-attached[" in run("show-hooks", "-t", "=seat-x:").stdout
    assert "client-attached[" not in run("show-hooks", "-g").stdout
