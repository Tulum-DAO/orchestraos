"""`orchestra rotate` refuses BEFORE it writes anything: the rotation's own early checks (a headless seat, a runtime
it does not support, no authoritative generation) run first (rotate_agent.py --preflight), so a refused rotate
leaves no baton, no prompt and no state behind. A stub rotate_agent.py stands in; nothing is spawned."""
import json

from orchestra_cli import __main__ as M
from orchestra_cli import seats as SE
from orchestra_cli.tests.test_seats import repo  # noqa: F401  (fixture)

REFUSING_STUB = (
    "import json, sys\n"
    "if '--preflight' in sys.argv:\n"
    "    print(json.dumps({'ok': False, 'refused': 'gm runs headless and cannot be rotated yet. Sample steps.'}))\n"
    "    sys.exit(3)\n"
    "print('rotate stub')\n")


def _files(root):
    return sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())


def test_a_refused_rotate_writes_no_baton_or_anything_else(repo, tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    (data / "registry.json").write_text(json.dumps({"agents": {"gm": {"name": "gm", "runtime": "claude",
                                                                      "generation": 1, "tmux_session": "gm"}}}))
    (repo / "scripts" / "rotate_agent.py").write_text(REFUSING_STUB)
    runs = []
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: runs.append(argv) or 0)
    before_data, before_repo = _files(data), _files(repo)
    assert M.main(["rotate", "gm", "--synthesize"]) == 2
    err = capsys.readouterr().err
    assert "rotate refused: gm runs headless and cannot be rotated yet. Sample steps." in err
    assert not (data / "docs" / "HANDOFF_gm-next.md").exists()
    assert _files(data) == before_data and _files(repo) == before_repo   # nothing new anywhere
    assert runs == []                                                    # the rotation itself never started


def test_a_rotate_that_may_go_ahead_still_writes_its_baton(repo, tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "registry.json").write_text(json.dumps({"agents": {"gm": {"name": "gm", "runtime": "claude",
                                                                      "generation": 1, "tmux_session": "gm"}}}))
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: 0)
    assert M.main(["rotate", "gm", "--synthesize", "--dry-run"]) == 0
    assert (data / "docs" / "HANDOFF_gm-next.md").exists()
