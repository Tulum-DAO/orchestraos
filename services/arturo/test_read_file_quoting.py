"""read_file's path is ONE shell word.

run_local runs shell=True, and read_file built `head -n {lines} {path}`, so a model-supplied path
like `x; touch <marker>` (prompt injection in anything Arturo reads) ran a second command.
By effect: the real tool against real files, and an injected marker that must never appear.
"""
import importlib.util
import pathlib

import pytest


def _load_proxy():
    spec = importlib.util.spec_from_file_location("arturo_proxy_rf", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def P():
    # A dashboard (fleet) turn: since the non-fleet allowlist, read_file is refused anywhere else, and a
    # refused tool would pass the injection test without ever building a command.
    from services.arturo.conftest import as_fleet
    return as_fleet(_load_proxy())


def _read(P, path, **kw):
    return P.execute_tool("read_file", {"path": path, **kw})


def test_an_injected_command_in_the_path_never_runs(P, tmp_path):
    marker = tmp_path / "PWNED"
    for evil in (f"x; touch {marker}", f"x && touch {marker}", f"$(touch {marker})",
                 f"`touch {marker}`", f"x | touch {marker}", f"x\ntouch {marker}"):
        _read(P, evil)
        assert not marker.exists(), evil


def test_a_plain_path_and_a_path_with_spaces_still_read(P, tmp_path):
    f = tmp_path / "a file.txt"
    f.write_text("hello\nworld\n")
    assert "hello" in _read(P, str(f))
    assert "File not found" in _read(P, str(tmp_path / "missing.txt"))


def test_tilde_still_expands_on_the_target(P, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "notes.md").write_text("from home\n")
    assert "from home" in _read(P, "~/notes.md")


def test_lines_is_a_bounded_integer(P, tmp_path):
    f = tmp_path / "n.txt"
    f.write_text("".join(f"{i}\n" for i in range(300)))
    out = _read(P, str(f), lines="5; touch x")
    assert "\n0\n" in "\n" + out.split(":\n", 1)[1] + "\n"
    assert out.count("\n") <= 60                      # fell back to 50, not an injected value
    assert len(_read(P, str(f), lines=10_000).splitlines()) <= 202


def test_shell_path_quoting(P):
    assert P._shell_path("~") == '"$HOME"'
    assert P._shell_path("~/a b") == "\"$HOME\"/'a b'"
    assert P._shell_path("/etc/x; id") == "'/etc/x; id'"


# ---- review of 26f7c0b + gm ruling ----

def test_a_path_with_a_nul_or_line_break_is_refused(P, tmp_path):
    for bad in ("a\nb", "a\rb", "a\x00b"):
        assert "Refused" in _read(P, bad)


def test_a_dash_leading_path_is_a_file_not_an_option(P, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "--help").write_text("i am a file\n")
    assert "i am a file" in _read(P, "--help")


@pytest.mark.parametrize("tool", ["get_agent_output", "kill_agent", "inject_message", "spawn_agent"])
def test_a_seat_name_with_shell_syntax_never_reaches_a_command(P, tool, tmp_path, monkeypatch):
    """session_name is pasted into tmux command strings (capture, kill, has-session, inject),
    run through a shell locally and over ssh. Refused at the tool entry, before any of them."""
    ran = []
    monkeypatch.setattr(P, "run_local", lambda cmd, timeout=15: ran.append(cmd) or (True, ""))
    monkeypatch.setattr(P, "ssh_mac", lambda cmd, timeout=10, **k: ran.append(cmd) or (True, ""))
    marker = tmp_path / "PWNED"
    out = P.execute_tool(tool, {"session_name": f"x; touch {marker}", "message": "hi", "machine": "vps"})
    assert "not a seat name" in out
    assert ran == [] and not marker.exists()


def test_ordinary_seat_names_pass_the_check(P):
    for ok in ("gm", "dev-first-project", "pm_ops", "ios-watch-dev"):
        assert P._SEAT_NAME_RE.match(ok)
