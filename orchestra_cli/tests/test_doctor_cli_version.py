"""G22: `orchestra doctor` reports the installed agent CLI version against the versions this release
was proven on. The harness reads the CLI's screen, transcripts and hook events; Claude Code 2.1.286
changed how a permission prompt is drawn and the menu bridge lost the command (fixed with the 2.1.295
captures). Nothing checked the version before this row. Advisory only: never blocks `orchestra up`.
"""
from orchestra_cli import doctor as D


def _probes(version_out="2.1.295 (Claude Code)", installed=("claude",), env=None):
    env = {"DISABLE_AUTOUPDATER": "1"} if env is None else env
    return D.DoctorProbes(
        which=lambda n: f"/usr/bin/{n}" if n in installed else None,
        run_cmd=lambda argv: version_out if argv[-1] == "--version" else "",
        port_owner=lambda p: None, supervisor_state=lambda d: None, import_ok=lambda m: True,
        read_file=lambda p: (_ for _ in ()).throw(FileNotFoundError(p)), now_ms=lambda: 0,
        python_version=(3, 12, 3), git_hooks_path=lambda r: ".git-hooks",
        env_get=lambda k: env.get(k))


def _rows(probes, enabled=("claude", "gemini", "codex")):
    return {c.name: c for c in D.cli_version_checks(probes, set(enabled))}


def test_the_pinned_version_is_ok():
    r = _rows(_probes())["cli:claude-version"]
    assert r.status == D.OK and "2.1.295" in r.detail


def test_the_reference_fleet_version_is_ok_too():
    assert _rows(_probes("2.1.284 (Claude Code)"))["cli:claude-version"].status == D.OK


def test_an_unproven_version_warns_with_the_exact_pin_line_and_never_blocks():
    r = _rows(_probes("2.1.301 (Claude Code)"))["cli:claude-version"]
    assert r.status == D.WARN and "2.1.301" in r.detail
    assert r.remedy == "sudo npm install -g @anthropic-ai/claude-code@2.1.295"
    assert r.required is False


def test_an_unreadable_version_warns():
    r = _rows(_probes("command failed"))["cli:claude-version"]
    assert r.status == D.WARN and r.required is False


def test_the_autoupdater_warns_until_disabled():
    rows = _rows(_probes(env={}))
    assert rows["cli:autoupdater"].status == D.WARN and rows["cli:autoupdater"].required is False
    assert "DISABLE_AUTOUPDATER=1" in rows["cli:autoupdater"].remedy
    assert "cli:autoupdater" not in _rows(_probes())


def test_no_row_for_a_cli_that_is_not_installed_or_not_enabled():
    assert _rows(_probes(installed=())) == {}
    assert _rows(_probes(), enabled=("gemini",)) == {}


def test_the_pin_is_the_first_proven_version():
    assert D.PROVEN_CLI_VERSIONS["claude"][0] == "2.1.295"
