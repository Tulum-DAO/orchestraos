"""Is an agent CLI running in a seat's pane? The process tree decides, not "any child".

Operator finding #10, 2026-10-08: gm's pane ran a nested `tmux attach` over a shell. Two
consumers read that wrong:
- orchestra_cli seats._pane_alive counted ANY child of the pane's shell as a live seat, so
  `orchestra starter` skipped a gm whose CLI was gone;
- spawn-agent.sh saw the session, said "already running" and returned, so neither starter nor
  the dashboard's Resume could ever relaunch a seat whose CLI had exited.

These tests run a REAL tmux through a `tmux` shim that pins `-S <private socket>`, with TMUX
removed from the environment, so nothing here can reach a live tmux server. The fake CLI is a
symlink named `claude` to sleep, so its argv[0] is `claude` exactly as a real one's is.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import pane_cli  # noqa: E402


def _real(name):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        cand = Path(d) / name
        if cand.is_file() and os.access(cand, os.X_OK) and not cand.read_bytes()[:2] == b"#!":
            return str(cand)
    return None


REAL_TMUX = _real("tmux")
pytestmark = pytest.mark.skipif(REAL_TMUX is None, reason="tmux binary not found")


@pytest.fixture()
def lab(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="pcl", dir="/tmp"))     # short: unix socket path limit
    sock, bindir = d / "s", d / "bin"
    bindir.mkdir()
    shim = bindir / "tmux"
    shim.write_text(f'#!/bin/sh\nexec "{REAL_TMUX}" -S "{sock}" "$@"\n')
    shim.chmod(0o755)
    (bindir / "claude").symlink_to(shutil.which("sleep"))
    os.mkfifo(bindir / "gemini")       # a node-wrapped CLI's script path: `cat` blocks on it
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("ORCH_RELAUNCH_MIN_AGE_S", "0")        # the startup-gap floor has its own test
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    assert shutil.which("tmux") == str(shim)                   # FENCE before any tmux call

    def tmux(*a):
        return subprocess.run(["tmux", *a], capture_output=True, text=True, timeout=10)

    tmux("-f", "/dev/null", "new-session", "-d", "-s", "base", "sleep 600")
    tmux.bindir = bindir
    try:
        yield tmux
    finally:
        subprocess.run([REAL_TMUX, "-S", str(sock), "kill-server"], capture_output=True, timeout=10)
        _kill_fake_clis(str(d))
        shutil.rmtree(d, ignore_errors=True)


def _kill_fake_clis(lab_dir=None):
    """A `claude 613 &` job outlives kill-server (it is reparented to init), and argv[0]
    `claude` makes it look like an agent to anything on the host that scans ps. Exact args
    only: a real CLI never runs as `claude 613`."""
    out = subprocess.run(["ps", "-e", "-o", "pid=,args="], capture_output=True, text=True).stdout
    for line in out.splitlines():
        pid, _, args = line.strip().partition(" ")
        if pid.isdigit() and (args.strip() == "claude 613" or (lab_dir and lab_dir in args)):
            try:
                os.kill(int(pid), 9)
            except OSError:
                pass


def _seat(tmux, name, keys):
    """A seat as spawn-agent.sh makes one: a shell pane, the command typed into it."""
    tmux("new-session", "-d", "-s", name, "-x", "100", "-y", "20", "sh")
    tmux("send-keys", "-t", f"={name}:", keys, "Enter")


def _until(pred, secs=5.0):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return pred()


def test_a_cli_under_the_shell_is_found(lab):
    _seat(lab, "s1", "claude 613")
    assert _until(lambda: pane_cli.pane_state("s1")["runtime"] == "claude")
    st = pane_cli.pane_state("s1")
    assert st["exists"] is True


def test_a_bare_shell_has_no_cli(lab):
    _seat(lab, "s2", "true")
    time.sleep(0.5)
    st = pane_cli.pane_state("s2")
    assert st["exists"] is True and st["runtime"] is None
    assert st["foreground"] == "sh"


def test_a_nested_tmux_client_over_a_shell_is_not_a_cli(lab):
    """Shaw's gm: pane_current_command 'tmux', nothing under it but the client."""
    _seat(lab, "s3", "env -u TMUX tmux attach -t =base:")
    assert _until(lambda: pane_cli.pane_state("s3")["foreground"] == "tmux")
    assert pane_cli.pane_state("s3")["runtime"] is None


def test_a_nested_client_launched_BY_the_cli_still_has_the_cli(lab):
    """The Bash-tool path: the CLI is alive underneath; the seat is not dead."""
    _seat(lab, "s4", "claude 613 & env -u TMUX tmux attach -t =base:")
    assert _until(lambda: pane_cli.pane_state("s4")["runtime"] == "claude")


def test_no_session_and_no_prefix_match(lab):
    _seat(lab, "s5x", "claude 613")
    st = pane_cli.pane_state("s5")
    assert st == {"exists": False, "runtime": None, "foreground": None}


def test_relaunchable_only_for_an_idle_shell_or_a_bare_nested_client(lab):
    _seat(lab, "r1", "true")                                  # CLI exited -> shell
    _seat(lab, "r2", "env -u TMUX tmux attach -t =base:")     # nested client over a shell
    _seat(lab, "r3", "claude 613")                            # live
    _seat(lab, "r4", "sleep 600")                             # something else the operator runs
    assert _until(lambda: pane_cli.pane_state("r2")["foreground"] == "tmux"
                  and pane_cli.pane_state("r3")["runtime"] == "claude"
                  and pane_cli.pane_state("r4")["foreground"] == "sleep")
    assert pane_cli.relaunchable(pane_cli.pane_state("r1")) is True
    assert pane_cli.relaunchable(pane_cli.pane_state("r2")) is True
    assert pane_cli.relaunchable(pane_cli.pane_state("r3")) is False
    assert pane_cli.relaunchable(pane_cli.pane_state("r4")) is False
    assert pane_cli.relaunchable(pane_cli.pane_state("gone")) is False


def test_cli_prints_json_and_relaunch_exit_code(lab):
    _seat(lab, "c1", "true")
    _seat(lab, "c2", "claude 613")
    assert _until(lambda: pane_cli.pane_state("c2")["runtime"] == "claude")
    time.sleep(0.3)
    run = lambda *a: subprocess.run([sys.executable, str(ROOT / "scripts" / "pane_cli.py"), *a],
                                    capture_output=True, text=True, timeout=10)
    out = run("c2")
    assert json.loads(out.stdout)["runtime"] == "claude" and out.returncode == 0
    assert run("--relaunchable", "c1").returncode == 0
    assert run("--relaunchable", "c2").returncode == 1


# ---- spawn-agent.sh: an existing session whose CLI is gone is relaunched, not skipped ----

GUARDS = ROOT / "scripts" / "spawn_guards.sh"


def _clear(name):
    return subprocess.run(["bash", "-c", f"set -euo pipefail; SCRIPT_DIR='{ROOT}'; "
                           f"err() {{ echo \"ERR: $*\" >&2; }}; source '{GUARDS}'; "
                           f"if clear_dead_session \"$1\"; then echo CLEARED; else echo KEPT; fi",
                           "_", name], capture_output=True, text=True, timeout=15)


def _has(tmux, name):
    return tmux("has-session", "-t", f"={name}").returncode == 0


def test_spawn_clears_a_session_whose_cli_exited_so_it_can_relaunch(lab):
    _seat(lab, "d1", "true")
    _seat(lab, "d2", "env -u TMUX tmux attach -t =base:")
    assert _until(lambda: pane_cli.pane_state("d2")["foreground"] == "tmux")
    for name in ("d1", "d2"):
        out = _clear(name)
        assert out.stdout.strip() == "CLEARED", out.stderr
        assert "relaunch" in out.stderr
        assert not _has(lab, name)


def test_spawn_never_clears_a_live_seat_or_the_operators_work(lab):
    _seat(lab, "k1", "claude 613")
    _seat(lab, "k2", "sleep 600")
    _seat(lab, "k3", "claude 613 & env -u TMUX tmux attach -t =base:")
    assert _until(lambda: pane_cli.pane_state("k1")["runtime"] == "claude"
                  and pane_cli.pane_state("k2")["foreground"] == "sleep"
                  and pane_cli.pane_state("k3")["runtime"] == "claude")
    for name in ("k1", "k2", "k3"):
        assert _clear(name).stdout.strip() == "KEPT"
        assert _has(lab, name)
    assert _clear("never-existed").stdout.strip() == "KEPT"


def test_orchestra_pane_alive_reads_the_process_tree(lab):
    """seats._pane_alive used to count ANY child of the pane shell, so a nested client over a
    shell read as a live gm and `orchestra starter` skipped it."""
    sys.path.insert(0, str(ROOT))
    from orchestra_cli import seats as SE
    _seat(lab, "a1", "claude 613")
    _seat(lab, "a2", "env -u TMUX tmux attach -t =base:")
    assert _until(lambda: pane_cli.pane_state("a1")["runtime"] == "claude"
                  and pane_cli.pane_state("a2")["foreground"] == "tmux")
    assert SE._pane_alive("a1") is True
    assert SE._pane_alive("a2") is False
    assert SE._pane_alive("nope") is False



def test_a_cli_in_another_window_or_pane_keeps_the_session(lab):
    """Review of the first version: only the current window's first pane was checked, and the
    WHOLE session was then killed. A shell the operator opened beside the CLI must not count."""
    _seat(lab, "w1", "claude 613")
    assert _until(lambda: pane_cli.pane_state("w1")["runtime"] == "claude")
    lab("new-window", "-t", "=w1:", "sh")                     # operator opens a shell window
    _seat(lab, "w2", "claude 613")
    assert _until(lambda: pane_cli.pane_state("w2")["runtime"] == "claude")
    lab("split-window", "-t", "=w2:", "sh")                   # a split; the shell pane is selected
    lab("select-pane", "-t", "=w2:.1")
    for name in ("w1", "w2"):
        st = pane_cli.pane_state(name)
        assert st["runtime"] == "claude", st
        assert pane_cli.relaunchable(st) is False
        assert _clear(name).stdout.strip() == "KEPT"
        assert _has(lab, name)


def test_a_session_inside_the_startup_gap_is_never_cleared(lab, monkeypatch):
    """spawn-agent.sh creates the session, then types the CLI into its shell a second or more
    later. A second spawn in that gap must not kill the first one's session."""
    monkeypatch.setenv("ORCH_RELAUNCH_MIN_AGE_S", "30")
    _seat(lab, "g1", "true")
    time.sleep(0.5)
    st = pane_cli.pane_state("g1")
    assert st["runtime"] is None and st["foreground"] == "sh"
    assert pane_cli.relaunchable(st) is False
    assert _clear("g1").stdout.strip() == "KEPT" and _has(lab, "g1")
    assert pane_cli.relaunchable(st, min_age_s=0) is True



def test_an_unreadable_process_table_is_never_relaunchable(lab, monkeypatch, tmp_path):
    """pm review of 4d750ff: ps failing or timing out read as "no CLI", and with a tmux or
    shell foreground (Shaw's exact state, which can also be a LIVE CLI running a nested
    client) that killed a live seat. Unknown must never relaunch."""
    _seat(lab, "u1", "true")
    time.sleep(0.5)
    assert pane_cli.relaunchable(pane_cli.pane_state("u1")) is True       # control: readable
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "ps").write_text("#!/bin/sh\nexit 1\n")
    (broken / "ps").chmod(0o755)
    monkeypatch.setenv("PATH", f"{broken}{os.pathsep}{os.environ['PATH']}")
    st = pane_cli.pane_state("u1")
    assert st["tree"] == "unknown"
    assert pane_cli.relaunchable(st) is False
    assert _clear("u1").stdout.strip() == "KEPT" and _has(lab, "u1")
    sys.path.insert(0, str(ROOT))
    from orchestra_cli import seats as SE
    assert SE._pane_alive("u1") is True, "unknown must read alive, so starter never relaunches"


def test_a_cli_this_file_cannot_name_still_keeps_the_session(lab):
    """A node-wrapped CLI whose name is in no table (here `node <lab>/bin/gemini`), running in
    the background under an idle shell: the foreground is the shell, the runtime is unknown,
    but something besides a shell is running, so the session is kept."""
    # bash, not the pane's sh: dash has no `exec -a`
    _seat(lab, "n1", f"bash -c 'exec -a node cat {lab.bindir}/gemini' &")
    assert _until(lambda: any(l.split(None, 1)[1:] == [f"node {lab.bindir}/gemini"] for l in
                              subprocess.run(["ps", "-e", "-o", "pid=,args="], capture_output=True,
                                             text=True).stdout.splitlines())), "fixture never ran"
    assert _until(lambda: pane_cli.pane_state("n1").get("busy") is True)
    st = pane_cli.pane_state("n1")
    assert st["runtime"] is None and st["foreground"] == "sh"
    assert pane_cli.relaunchable(st) is False
    assert _clear("n1").stdout.strip() == "KEPT"


def test_an_interpreter_wrapped_known_cli_is_named(lab):
    (lab.bindir / "codex").symlink_to(lab.bindir / "gemini")       # a path named codex
    _seat(lab, "n2", f"bash -c 'exec -a node cat {lab.bindir}/codex'")
    assert _until(lambda: pane_cli.pane_state("n2")["runtime"] == "codex")


def test_a_missing_creation_time_is_never_relaunchable():
    st = {"exists": True, "tree": "ok", "busy": False, "runtime": None,
          "foreground": "bash", "foregrounds": ["bash"], "created": 0}
    assert pane_cli.relaunchable(st, min_age_s=0) is False
    assert pane_cli.relaunchable({**st, "created": 1}, min_age_s=0) is True
