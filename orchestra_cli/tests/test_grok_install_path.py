"""Grok installed only in ~/.grok/bin is found (a new user's box, 2026-10-14).

The Grok installer puts its launcher in ~/.grok/bin. docs/INSTALL.md said it links `grok` into
~/.local/bin, but on that box it did not, so the systemd unit's PATH (and `which grok`) never saw
it and every check called Grok "not installed". Now `orchestra init` links ~/.local/bin/grok to it
(never touching an existing ~/.local/bin/grok), doctor says when the link is missing, and
spawn-agent.sh also looks in ~/.grok/bin.
"""
import os
import subprocess
from pathlib import Path

from orchestra_cli import doctor as D
from orchestra_cli import init_cmd as I

CHECKOUT = Path(__file__).resolve().parents[2]


def _grok_in_dot_grok(home):
    b = home / ".grok" / "bin"; b.mkdir(parents=True)
    g = b / "grok"; g.write_text("#!/bin/sh\necho grok\n"); g.chmod(0o755)
    return g


def test_init_links_local_bin_grok_to_dot_grok(tmp_path):
    real = _grok_in_dot_grok(tmp_path)
    step = I.grok_link_step(tmp_path, which=lambda name: None)
    link = tmp_path / ".local" / "bin" / "grok"
    assert step.did and os.readlink(link) == str(real)
    again = I.grok_link_step(tmp_path, which=lambda name: None)     # idempotent
    assert not again.did and os.readlink(link) == str(real)


def test_init_never_touches_an_existing_local_bin_grok(tmp_path):
    _grok_in_dot_grok(tmp_path)
    mine = tmp_path / ".local" / "bin"; mine.mkdir(parents=True)
    (mine / "grok").write_text("#!/bin/sh\necho mine\n")
    step = I.grok_link_step(tmp_path, which=lambda name: None)
    assert not step.did and (mine / "grok").read_text() == "#!/bin/sh\necho mine\n"


def test_init_does_nothing_when_grok_is_absent_or_already_on_path(tmp_path):
    assert I.grok_link_step(tmp_path, which=lambda name: None) is None          # not installed
    _grok_in_dot_grok(tmp_path)
    step = I.grok_link_step(tmp_path, which=lambda name: "/opt/grok/bin/grok")  # on PATH already
    assert step is None and not (tmp_path / ".local" / "bin" / "grok").exists()


def test_doctor_warns_when_grok_is_only_in_dot_grok(tmp_path):
    _grok_in_dot_grok(tmp_path)
    row = D.grok_path_check(lambda name: None, tmp_path)
    assert row is not None and row.status == D.WARN and "orchestra init" in row.remedy
    assert D.grok_path_check(lambda name: str(tmp_path / ".local/bin/grok"), tmp_path) is None
    assert D.grok_path_check(lambda name: None, tmp_path / "nohome") is None


def _spawn_grok_bin(home, path="/usr/bin:/bin"):
    """Run exactly the GROK_BIN lines of spawn-agent.sh (the binary pick, nothing else)."""
    lines = [l for l in (CHECKOUT / "spawn-agent.sh").read_text().splitlines()
             if l.startswith("GROK_BIN=") or (l.startswith("[[") and "GROK_BIN" in l)]
    assert len(lines) >= 2, lines
    r = subprocess.run(["bash", "-c", "\n".join(lines) + '\necho "$GROK_BIN"'],
                       capture_output=True, text=True, env={"HOME": str(home), "PATH": path})
    return r.stdout.strip()


def test_spawn_agent_finds_grok_in_dot_grok_when_nothing_else_has_it(tmp_path):
    real = _grok_in_dot_grok(tmp_path)
    assert _spawn_grok_bin(tmp_path) == str(real)


def test_spawn_agent_still_prefers_local_bin_then_path(tmp_path):
    _grok_in_dot_grok(tmp_path)
    onpath = tmp_path / "p"; onpath.mkdir()
    (onpath / "grok").write_text("#!/bin/sh\n"); (onpath / "grok").chmod(0o755)
    assert _spawn_grok_bin(tmp_path, f"{onpath}:/usr/bin:/bin") == str(onpath / "grok")
    lb = tmp_path / ".local" / "bin"; lb.mkdir(parents=True)
    (lb / "grok").write_text("#!/bin/sh\n"); (lb / "grok").chmod(0o755)
    assert _spawn_grok_bin(tmp_path, f"{onpath}:/usr/bin:/bin") == str(lb / "grok")


def test_spawn_agent_with_no_grok_anywhere_keeps_the_bare_name(tmp_path):
    assert _spawn_grok_bin(tmp_path) == "grok"


def test_the_service_unit_path_carries_dot_grok_bin_when_it_exists(tmp_path):
    from orchestra_cli import install_unit as U
    home = str(tmp_path)
    without = U.build_path("/usr/bin", home, exists=lambda p: False).split(os.pathsep)
    assert f"{home}/.grok/bin" not in without
    with_ = U.build_path("/usr/bin", home, exists=lambda p: p == f"{home}/.grok/bin").split(os.pathsep)
    assert with_.index(f"{home}/.local/bin") < with_.index(f"{home}/.grok/bin") < with_.index("/usr/bin")


# --- codex review round 1 -----------------------------------------------------------------------
def test_an_executable_orchestra_grok_bin_wins_over_every_other_location(tmp_path):
    _grok_in_dot_grok(tmp_path)
    lb = tmp_path / ".local" / "bin"; lb.mkdir(parents=True)
    (lb / "grok").write_text("#!/bin/sh\n"); (lb / "grok").chmod(0o755)
    pin = tmp_path / "pinned-grok"; pin.write_text("#!/bin/sh\n"); pin.chmod(0o755)
    lines = [l for l in (CHECKOUT / "spawn-agent.sh").read_text().splitlines()
             if l.startswith("GROK_BIN=") or (l.startswith("[[") and "GROK_BIN" in l)]
    r = subprocess.run(["bash", "-c", "\n".join(lines) + '\necho "$GROK_BIN"'], capture_output=True, text=True,
                       env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "ORCHESTRA_GROK_BIN": str(pin)})
    assert r.stdout.strip() == str(pin)


def test_supervised_processes_get_local_bin_and_dot_grok_bin_on_path(tmp_path, monkeypatch):
    """The API decides "installed" with `which grok` and runs `grok models` on ITS PATH, which under
    `orchestra up` is the shell's: without ~/.local/bin there, the init link was invisible to it."""
    from orchestra_cli import settings as S
    _grok_in_dot_grok(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / "orchestra.toml"; cfg.write_text(f'[data]\ndir = "{tmp_path / "data"}"\n')
    st = S.load_settings(config_path=cfg)
    # child_env puts the checkout's own .venv/bin first when it exists (the right python for every
    # child); that is set aside here, so the test holds on a checkout with or without a venv.
    venv = str(st.venv_dir / "bin")
    def child_path(base):
        return [p for p in S.child_env(st, base={"PATH": base})["PATH"].split(os.pathsep) if p != venv]
    path = child_path("/usr/bin:/bin")
    assert path[:2] == ["/usr/bin", "/bin"]                                  # the caller's order is kept
    assert f"{tmp_path}/.local/bin" in path and f"{tmp_path}/.grok/bin" in path
    again = child_path(f"{tmp_path}/.local/bin:/usr/bin")
    assert again.count(f"{tmp_path}/.local/bin") == 1
