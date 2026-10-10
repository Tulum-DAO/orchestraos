"""RED-first (PR 0): a Codex seat starts on the first try.

Gate container, codex-cli 0.153.4 (2026-10-10): `codex --yolo` still stops at "Do you trust the
contents of this directory?" (fixtures/codex/trust_prompt_0.153.4.pane.txt, captured from the
real CLI). The boot instruction landed on that menu, Codex quit, and the seat was a bare shell.
spawn-agent.sh now writes `[projects."<seat cwd>"] trust_level = "trusted"` into
$CODEX_HOME/config.toml first (scripts/codex_trust.py), under gm's PR 0 conditions:
only the exact (symlink-resolved) seat dir, never / or $HOME or a parent of it; merge without
clobbering keys or comments; atomic; idempotent; first change backed up.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TOOL = HERE / "codex_trust.py"
FIXTURE = HERE / "fixtures" / "codex" / "trust_prompt_0.153.4.pane.txt"


@pytest.fixture
def box(tmp_path, monkeypatch):
    home = tmp_path / "home"; home.mkdir()
    codex_home = home / ".codex"
    seat = tmp_path / "work" / "seat"; seat.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return type("Box", (), {"home": home, "codex_home": codex_home, "cfg": codex_home / "config.toml",
                            "seat": seat, "tmp": tmp_path})


def trust(arg, env=None):
    return subprocess.run([sys.executable, str(TOOL), str(arg)], capture_output=True, text=True,
                          env=env or os.environ.copy(), timeout=30)


def trusted(cfg: Path, d: Path) -> bool:
    data = tomllib.loads(cfg.read_text())
    return data.get("projects", {}).get(os.path.realpath(d), {}).get("trust_level") == "trusted"


# --- writes the one key -------------------------------------------------------------------

def test_fresh_box_gets_the_seat_dir_trusted(box):
    r = trust(box.seat)
    assert r.returncode == 0, r.stderr
    assert trusted(box.cfg, box.seat)
    assert stat.S_IMODE(os.stat(box.cfg).st_mode) == 0o600
    assert "trusted" in r.stderr


def test_existing_keys_and_comments_are_kept_byte_for_byte(box):
    box.codex_home.mkdir()
    original = ('# my codex settings\nmodel = "gpt-5.6-terra"   # keep me\n\n'
                '[projects."/somewhere/else"]\ntrust_level = "trusted"\n\n'
                '[mcp_servers.thing]\ncommand = "thing"\nargs = ["--x"]  # comment\n')
    box.cfg.write_text(original)
    os.chmod(box.cfg, 0o640)
    assert trust(box.seat).returncode == 0
    after = box.cfg.read_text()
    assert after.startswith(original), "every existing line must survive unchanged"
    assert trusted(box.cfg, box.seat)
    data = tomllib.loads(after)
    assert data["model"] == "gpt-5.6-terra" and data["mcp_servers"]["thing"]["args"] == ["--x"]
    assert data["projects"]["/somewhere/else"]["trust_level"] == "trusted"
    assert stat.S_IMODE(os.stat(box.cfg).st_mode) == 0o640, "the file's mode is kept"


def test_an_existing_untrusted_entry_changes_exactly_one_line(box):
    box.codex_home.mkdir()
    seat = os.path.realpath(box.seat)
    original = (f'# top\n[projects."{seat}"]\ntrust_level = "untrusted"  # was asked before\n'
                f'other = 1\n\n[projects."/x"]\ntrust_level = "untrusted"\n')
    box.cfg.write_text(original)
    assert trust(box.seat).returncode == 0
    before, after = original.splitlines(), box.cfg.read_text().splitlines()
    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(before) == len(after) and changed == [
        ('trust_level = "untrusted"  # was asked before', 'trust_level = "trusted"  # was asked before')]
    assert tomllib.loads(box.cfg.read_text())["projects"]["/x"]["trust_level"] == "untrusted"


def test_running_twice_changes_nothing(box):
    assert trust(box.seat).returncode == 0
    first = box.cfg.read_bytes(); m1 = os.stat(box.cfg).st_mtime_ns
    r = trust(box.seat)
    assert r.returncode == 0 and r.stderr == ""
    assert box.cfg.read_bytes() == first and os.stat(box.cfg).st_mtime_ns == m1


def test_first_change_backs_up_the_original_once(box):
    box.codex_home.mkdir()
    box.cfg.write_text('model = "a"\n')
    other = box.tmp / "work" / "other"; other.mkdir()
    assert trust(box.seat).returncode == 0
    bak = Path(str(box.cfg) + ".orchestra-backup")
    assert bak.read_text() == 'model = "a"\n'
    assert trust(other).returncode == 0
    assert bak.read_text() == 'model = "a"\n', "the backup is never overwritten"
    assert trusted(box.cfg, box.seat) and trusted(box.cfg, other)


def test_no_backup_when_there_was_no_file(box):
    assert trust(box.seat).returncode == 0
    assert not Path(str(box.cfg) + ".orchestra-backup").exists()


def test_symlinked_seat_dir_is_trusted_by_its_real_path(box):
    link = box.tmp / "link"; link.symlink_to(box.seat)
    assert trust(link).returncode == 0
    projects = tomllib.loads(box.cfg.read_text())["projects"]
    assert os.path.realpath(box.seat) in projects and str(link) not in projects


def test_CODEX_HOME_is_honoured(box):
    alt = box.tmp / "alt-codex"
    env = dict(os.environ, CODEX_HOME=str(alt))
    assert trust(box.seat, env).returncode == 0
    assert trusted(alt / "config.toml", box.seat) and not box.cfg.exists()


def test_no_temp_files_are_left_behind(box):
    assert trust(box.seat).returncode == 0
    assert sorted(p.name for p in box.codex_home.iterdir()) == [".orchestra-trust.lock", "config.toml"]


# --- refuses anything wider than one seat dir ----------------------------------------------

@pytest.mark.parametrize("which", ["root", "home", "parent-of-home", "glob", "missing", "empty"])
def test_refuses_and_writes_nothing(box, which):
    box.codex_home.mkdir()
    box.cfg.write_text('model = "keep"\n')
    before = box.cfg.read_bytes()
    arg = {"root": "/", "home": str(box.home), "parent-of-home": str(box.home.parent),
           "glob": str(box.tmp / "work" / "*"), "missing": str(box.tmp / "nope"), "empty": ""}[which]
    r = trust(arg)
    assert r.returncode == 3, r.stderr
    assert r.stderr.startswith("codex_trust: refused: ")
    assert box.cfg.read_bytes() == before
    assert not Path(str(box.cfg) + ".orchestra-backup").exists()


def test_home_reached_through_a_symlink_is_refused(box):
    link = box.tmp / "home-link"; link.symlink_to(box.home)
    assert trust(link).returncode == 3


@pytest.mark.parametrize("bad", ["model = \n", 'projects = { "/a" = { trust_level = "trusted" } }\n'])
def test_a_file_it_cannot_merge_safely_is_left_alone(box, bad):
    """Invalid TOML, or `projects` written as an inline table (a [projects."x"] header would be a
    redefinition): refuse with the reason rather than rewrite the user's file."""
    box.codex_home.mkdir()
    box.cfg.write_text(bad)
    r = trust(box.seat)
    assert r.returncode == 3 and "refused" in r.stderr
    assert box.cfg.read_text() == bad


# --- by effect, through spawn-agent.sh, against the REAL captured trust prompt --------------

FAKE_CODEX = r'''#!/usr/bin/env python3
# Stand-in for codex 0.153.4's first screen: the real trust prompt (captured) when the cwd is not
# trusted in $CODEX_HOME/config.toml, else the composer.
import os, sys, tomllib
home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
try:
    cfg = tomllib.load(open(os.path.join(home, "config.toml"), "rb"))
except FileNotFoundError:
    cfg = {}
here = os.path.realpath(os.getcwd())
ok = cfg.get("projects", {}).get(here, {}).get("trust_level") == "trusted"
sys.stdout.write("› Ask Codex to do anything\n" if ok else open(sys.argv[1]).read())
'''


def _spawn_codex(box, cwd):
    """Run the real spawn-agent.sh for a codex seat up to the pane (tmux shim refuses new-session,
    so nothing launches) and return (result, the screen the stand-in codex then shows in cwd)."""
    data, binn = box.tmp / "data", box.tmp / "bin"
    (data / "state").mkdir(parents=True); binn.mkdir()
    (data / "registry.json").write_text(json.dumps({"agents": {"cx": {
        "name": "cx", "tier": "T1", "role": "worker", "runtime": "codex", "model": "gpt-5.6-terra",
        "tmux_session": "cx", "cwd": str(cwd), "machine": "local"}}}))
    tm = binn / "tmux"; tm.write_text('#!/bin/sh\ncase "$1" in new-session|has-session) exit 1;; *) exit 0;; esac\n')
    tm.chmod(0o755)
    cx = binn / "codex"; cx.write_text(FAKE_CODEX); cx.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in (
        "TMUX", "PARENT_AGENT_ID", "AGENT_TIER", "AGENT_ROLE", "REGISTRY", "REGISTRY_PATH", "CODEX_HOME",
        "ORCH_DIR")}
    # ORCHESTRA_CONFIG -> a missing file: scripts/orchestra-env.sh would otherwise export the
    # checkout's orchestra.toml [data] dir OVER this ORCHESTRA_DIR (reported fence leak).
    env.update(PATH=f"{binn}:{env['PATH']}", HOME=str(box.home), ORCHESTRA_DIR=str(data),
               ORCHESTRA_CONFIG=str(box.tmp / "no-such-orchestra.toml"),
               AGENT_RUNTIME="codex", AGENT_MODEL="gpt-5.6-terra")
    r = subprocess.run(["bash", str(ROOT / "spawn-agent.sh"), "cx"], env=env, capture_output=True,
                       text=True, timeout=120, cwd=str(ROOT))
    screen = subprocess.run([str(cx), str(FIXTURE)], cwd=str(cwd), env=env, capture_output=True,
                            text=True, timeout=30).stdout
    return r, screen


def test_the_fixture_is_the_real_trust_prompt():
    t = FIXTURE.read_text()
    assert "Do you trust the contents of this directory?" in t and "1. Yes, continue" in t and "2. No, quit" in t


def test_BY_EFFECT_a_spawned_codex_seat_does_not_meet_the_trust_prompt(box):
    r, screen = _spawn_codex(box, box.seat)
    assert "Do you trust the contents of this directory?" not in screen, (
        "codex would stop at its trust prompt and quit on the boot text:\n" + r.stdout + r.stderr)
    assert "Ask Codex to do anything" in screen
    assert trusted(box.cfg, box.seat)


def test_BY_EFFECT_spawn_refuses_a_codex_seat_in_HOME_before_any_pane(box):
    r, screen = _spawn_codex(box, box.home)
    assert r.returncode == 3
    assert "spawn REFUSED" in r.stderr and "home directory" in r.stderr
    assert not box.cfg.exists()


def test_BY_EFFECT_a_claude_seat_never_touches_codex_config(box):
    data, binn = box.tmp / "data", box.tmp / "bin"
    (data / "state").mkdir(parents=True); binn.mkdir()
    (data / "registry.json").write_text(json.dumps({"agents": {"cl": {
        "name": "cl", "tier": "T1", "role": "worker", "runtime": "claude", "model": "claude-opus-4-8",
        "tmux_session": "cl", "cwd": str(box.seat), "machine": "local"}}}))
    tm = binn / "tmux"; tm.write_text('#!/bin/sh\ncase "$1" in new-session|has-session) exit 1;; *) exit 0;; esac\n')
    tm.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "REGISTRY", "REGISTRY_PATH", "CODEX_HOME", "ORCH_DIR")}
    env.update(PATH=f"{binn}:{env['PATH']}", HOME=str(box.home), ORCHESTRA_DIR=str(data),
               ORCHESTRA_CONFIG=str(box.tmp / "no-such-orchestra.toml"),
               AGENT_RUNTIME="claude", AGENT_MODEL="claude-opus-4-8")
    subprocess.run(["bash", str(ROOT / "spawn-agent.sh"), "cl"], env=env, capture_output=True, text=True,
                   timeout=120, cwd=str(ROOT))
    assert not box.codex_home.exists(), "the claude path must not create or edit Codex's config"
