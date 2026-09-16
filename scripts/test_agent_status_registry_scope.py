"""RED-first: `agent-status.py --all` is registry-scoped by default (gm ruling msg_9f04c5f0).

tmux is host-global: --all listed every session on the machine with a claude process,
so a second OrchestraOS instance beside a live fleet observed (and its beat acted on)
foreign seats. The pure helper keeps only sessions that are a registry agent id or a
registry row's tmux_session; `--all-sessions` is the explicit host-wide escape hatch.
"""
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _mod():
    spec = importlib.util.spec_from_file_location("agent_status_mod", os.path.join(_HERE, "agent-status.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_scope_sessions_to_registry_keeps_ids_and_tmux_session_aliases():
    m = _mod()
    reg = {"agents": {"gm": {}, "ob": {"tmux_session": "ob-gen44"}}}
    out = m.scope_sessions_to_registry(["gm", "foreign-shell", "ob-gen44", "session-3"], reg)
    assert out == ["gm", "ob-gen44"]


def test_scope_sessions_to_registry_empty_registry_yields_nothing():
    m = _mod()
    assert m.scope_sessions_to_registry(["a", "b"], {"agents": {}}) == []
    assert m.scope_sessions_to_registry(["a", "b"], {}) == []


def test_all_flag_parsing_scoped_by_default_and_all_sessions_escape_hatch():
    m = _mod()
    assert m.parse_all_mode(["--all"]) == "registry"
    assert m.parse_all_mode(["--all", "--all-sessions"]) == "host"
    assert m.parse_all_mode(["gm"]) is None


def test_all_with_no_live_registered_seats_prints_empty_json_array_and_exits_zero(tmp_path):
    """The rotation beat (lineage-daemon.gather_status) runs `agent-status.py --all` with
    check_output and json.loads: a clean install has no live seat yet, and a non-zero exit /
    prose stdout crashed cron_beat at its first tick under `orchestra up` (seen 2026-09-16).
    Empty fleet = `[]` on stdout, exit 0; the human hint goes to stderr."""
    import json
    import subprocess
    (tmp_path / "registry.json").write_text(json.dumps({"agents": {}}))
    r = subprocess.run([sys.executable, os.path.join(_HERE, "agent-status.py"), "--all"],
                       capture_output=True, text=True, timeout=60,
                       env={**os.environ, "ORCH_DIR": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == []
    assert "registered" in r.stderr.lower()
