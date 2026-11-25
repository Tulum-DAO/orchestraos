"""Orphaned browser-plugin Chrome: a live parent is spared, a dead parent is reaped, Chrome outside
the plugin's profile root is never touched.

Unit cases run classify() on a fake process table. The by-effect cases start real processes named
`chrome` and run scripts/chrome-reaper.sh itself, with the profile root and log in a temp dir and an
age limit no real browser can reach, so nothing outside the test is eligible.
"""
import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import chrome_orphans as co  # noqa: E402

ROOT = "/r/browser-profiles"
MCP = (1000, "node", ["node", "/x/superpowers-chrome/1.6.1/mcp/dist/index.js"])


def _chrome(ppid, profile=ROOT + "/seat", extra=()):
    return (ppid, "chrome", ["/snap/chromium/chrome", f"--user-data-dir={profile}", "--headless=new", *extra])


def _verdicts(procs, adopters=()):
    return {pid: v for pid, _prof, v, _why in co.classify(procs, [ROOT], adopters)}


def test_a_chrome_with_its_live_plugin_mcp_parent_is_spared():
    assert _verdicts({50: MCP, 51: _chrome(50)}) == {51: "spared"}


def test_a_chrome_whose_parent_is_gone_is_an_orphan():
    assert _verdicts({51: _chrome(50)}) == {51: "orphan"}


def test_a_chrome_reparented_to_pid_1_is_an_orphan():
    assert _verdicts({51: _chrome(1)}) == {51: "orphan"}


def test_a_chrome_reparented_to_systemd_user_is_an_orphan():
    procs = {900: (1, "systemd", ["/usr/lib/systemd/systemd", "--user"]), 51: _chrome(900)}
    assert _verdicts(procs) == {51: "orphan"}


def test_a_chrome_with_any_other_live_parent_is_spared():
    procs = {70: (1, "bash", ["bash"]), 51: _chrome(70)}
    assert _verdicts(procs) == {51: "spared"}


def test_chrome_outside_the_plugin_root_is_never_reported():
    procs = {51: _chrome(1, profile="/home/u/.config/chromium"), 52: (1, "chrome", ["chrome", "--headless"])}
    assert _verdicts(procs) == {}


def test_renderers_and_helpers_are_not_main_processes():
    assert _verdicts({51: _chrome(1, extra=("--type=renderer",))}) == {}


def test_both_spellings_of_the_root_match(tmp_path):
    real = tmp_path / "real-profiles"
    (real / "seat").mkdir(parents=True)
    link = tmp_path / "browser-profiles"
    link.symlink_to(real)
    procs = {51: _chrome(1, profile=str(real / "seat")), 52: _chrome(1, profile=str(link / "seat"))}
    rows = co.classify(procs, [os.path.realpath(str(link))])
    assert sorted(p for p, _, v, _ in rows if v == "orphan") == [51, 52]


# ───────── by effect: real processes, the real script ─────────

REAPER = os.path.join(HERE, "chrome-reaper.sh")

# Runs as its own process: becomes a child subreaper, starts fake browsers whose launcher exits at
# once (so they are reparented to the driver, which stands in for pid 1 / systemd --user), runs the
# reaper, and reports which pids are still alive.
DRIVER = textwrap.dedent(r'''
    import ctypes, json, os, subprocess, sys, time
    ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)          # PR_SET_CHILD_SUBREAPER
    fake, root, other, log = sys.argv[1:5]
    QUIET = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}

    def orphaned(profile):
        r, w = os.pipe()
        if os.fork() == 0:                            # launcher: start the browser, report, exit
            os.close(r)
            p = subprocess.Popen([fake, f"--user-data-dir={profile}"], start_new_session=True, **QUIET)
            os.write(w, str(p.pid).encode()); os._exit(0)
        os.close(w); pid = int(os.read(r, 32)); os.close(r)
        os.wait()                                     # the launcher is gone: the browser is ours now
        return pid

    plugin = orphaned(os.path.join(root, "seat-a"))
    outside = orphaned(other)
    # its parent is a live shell (the trailing ":" stops sh from exec-ing it), not the adopter
    live_sh = subprocess.Popen(["sh", "-c", f'"{fake}" --user-data-dir="{os.path.join(root, "seat-b")}"; :'],
                               start_new_session=True, **QUIET)
    time.sleep(0.3)
    live_pid = int(subprocess.run(["pgrep", "-P", str(live_sh.pid)], capture_output=True,
                                  text=True).stdout.split()[0])
    time.sleep(0.3)
    env = {**os.environ, "CHROME_REAPER_PROFILE_ROOTS": root, "CHROME_REAPER_LOG": log,
           "CHROME_REAPER_ADOPTERS": str(os.getpid())}
    subprocess.run(["bash", sys.argv[5], "999999999"], env=env, check=True)

    def alive(pid):
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        try:
            with open(f"/proc/{pid}/stat") as fh:
                return fh.read().split(")")[-1].split()[0] != "Z"
        except OSError:
            return False

    out = {"plugin_orphan": alive(plugin), "outside_root": alive(outside), "live_parent": alive(live_pid)}
    for pid in (plugin, outside, live_sh.pid):           # each browser leads its own process group
        try: os.killpg(pid, 9)
        except OSError: pass
    print(json.dumps(out))
''')


@pytest.fixture
def fake_chrome(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "chrome"                          # comm of a shebang script = its file name
    fake.write_text("#!/bin/sh\nsleep 300\n")
    fake.chmod(0o755)
    return fake


def test_the_reaper_ends_only_the_orphaned_plugin_chrome(tmp_path, fake_chrome):
    root = tmp_path / "browser-profiles"
    root.mkdir()
    log = tmp_path / "reaper.log"
    r = subprocess.run([sys.executable, "-c", DRIVER, str(fake_chrome), str(root),
                        str(tmp_path / "elsewhere"), str(log), REAPER],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    alive = json.loads(r.stdout.strip().splitlines()[-1])
    assert alive == {"plugin_orphan": False, "outside_root": True, "live_parent": True}
    lines = [l for l in log.read_text().splitlines() if "ORPHAN" in l]
    assert len(lines) == 1 and "seat-a" in lines[0] and "why=reparented to adopter" in lines[0]


def test_list_mode_kills_nothing_and_shows_the_verdict(tmp_path, fake_chrome):
    root = tmp_path / "browser-profiles"
    root.mkdir()
    p = subprocess.Popen([str(fake_chrome), f"--user-data-dir={root / 'seat'}"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.3)
        env = {**os.environ, "CHROME_REAPER_PROFILE_ROOTS": str(root),
               "CHROME_REAPER_LOG": str(tmp_path / "reaper.log")}
        r = subprocess.run(["bash", REAPER, "--list", "999999999"], env=env, capture_output=True,
                           text=True, timeout=30)
        assert f"spared\t{p.pid}\t" in r.stdout
        assert p.poll() is None
        assert not (tmp_path / "reaper.log").exists()
    finally:
        os.killpg(p.pid, 9)
        p.wait()
