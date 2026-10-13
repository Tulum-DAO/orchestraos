"""`orchestra doctor` rows for a Codex-only install: the installed codex version against the one this
release was proven on (the real captures under scripts/fixtures/codex are 0.153.4), no Claude-only
autoupdater advice on a box without claude, and an honest hook-layer row for codex.
"""
from orchestra_cli import doctor as D


def _probes(versions, installed, env=None):
    env = {} if env is None else env

    def run_cmd(argv):
        return versions.get(argv[0], "") if argv[-1] == "--version" else ""
    return D.DoctorProbes(
        which=lambda n: f"/usr/bin/{n}" if n in installed else None,
        run_cmd=run_cmd,
        port_owner=lambda p: None, supervisor_state=lambda d: None, import_ok=lambda m: True,
        read_file=lambda p: (_ for _ in ()).throw(FileNotFoundError(p)), now_ms=lambda: 0,
        python_version=(3, 12, 3), git_hooks_path=lambda r: ".git-hooks",
        env_get=lambda k: env.get(k))


def _rows(probes, enabled=("claude", "gemini", "codex")):
    return {c.name: c for c in D.cli_version_checks(probes, set(enabled))}


def test_the_proven_codex_version_is_ok():
    r = _rows(_probes({"codex": "codex-cli 0.153.4"}, installed=("codex",)))["cli:codex-version"]
    assert r.status == D.OK and "0.153.4" in r.detail


def test_an_unproven_codex_version_warns_with_the_npm_pin_and_never_blocks():
    r = _rows(_probes({"codex": "codex-cli 0.162.1"}, installed=("codex",)))["cli:codex-version"]
    assert r.status == D.WARN and "0.162.1" in r.detail and r.required is False
    assert r.remedy == "sudo npm install -g @openai/codex@0.153.4"


def test_a_codex_only_box_gets_no_claude_autoupdater_advice():
    """DISABLE_AUTOUPDATER is Claude Code's switch; a codex seat is pinned per run with
    -c check_for_update_on_startup=false instead (spawn-agent.sh)."""
    rows = _rows(_probes({"codex": "codex-cli 0.153.4"}, installed=("codex",), env={}))
    assert "cli:autoupdater" not in rows
    both = _rows(_probes({"codex": "codex-cli 0.153.4", "claude": "2.1.295 (Claude Code)"},
                         installed=("codex", "claude"), env={}))
    assert both["cli:autoupdater"].status == D.WARN     # claude installed: the advice still applies


def test_no_codex_row_when_codex_is_not_enabled_or_not_installed():
    assert "cli:codex-version" not in _rows(_probes({"codex": "codex-cli 0.153.4"}, installed=("codex",)),
                                            enabled=("claude",))
    assert "cli:codex-version" not in _rows(_probes({}, installed=()))


def test_the_codex_hook_layer_row_says_so_honestly():
    rows = {c.name: c for c in D.hook_layer_checks(_probes({}, installed=("codex",)), {"codex"})}
    r = rows["hooks:codex"]
    assert r.status == D.INFO and r.required is False
    assert "no hook layer" in r.detail.lower() and "HOOKS.md" in r.detail


def test_no_codex_hook_row_when_codex_is_absent():
    assert D.hook_layer_checks(_probes({}, installed=("claude",)), {"claude"}) == []
    assert D.hook_layer_checks(_probes({}, installed=("codex",)), {"claude"}) == []     # not enabled
    assert D.hook_layer_checks(_probes({}, installed=("claude",)), {"codex"}) == []     # enabled, not installed


def test_the_hook_row_claims_only_what_is_certain():
    """No Stop event is a fact; "does not rotate or hand off by itself" was a negative claim nobody
    had proven (orchestraos-builder review), so the row does not make it."""
    r = D.hook_layer_checks(_probes({}, installed=("codex",)), {"codex"})[0]
    assert "Stop event" in r.detail
    assert "rotat" not in r.detail.lower() and "hand off" not in r.detail.lower()
