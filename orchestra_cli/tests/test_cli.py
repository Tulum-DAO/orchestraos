"""RED-first: argument parsing + dry-run never spawns anything."""
import pytest

from orchestra_cli import __main__ as M


def test_parse_subcommands():
    ns = M.parse_args(["up", "--dry-run"])
    assert ns.command == "up" and ns.dry_run is True
    ns = M.parse_args(["doctor", "--json"])
    assert ns.command == "doctor" and ns.json is True
    ns = M.parse_args(["init", "--data-dir", "/tmp/x", "--no-npm", "--no-venv", "--no-build"])
    assert ns.command == "init" and ns.data_dir == "/tmp/x" and ns.no_npm and ns.no_venv and ns.no_build
    for cmd in ("down", "status"):
        assert M.parse_args([cmd]).command == cmd


def test_unknown_subcommand_exits_2():
    with pytest.raises(SystemExit) as e:
        M.parse_args(["frobnicate"])
    assert e.value.code == 2


def test_up_dry_run_prints_table_without_spawning(tmp_path, capsys, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "orchestra.toml").write_text(
        f'[data]\ndir = "{tmp_path / "data"}"\n'
        '[gateway]\nhost = "127.0.0.1"\nport = 8890\n'
        '[dashboard]\nhost = "127.0.0.1"\nport = 8891\n'
        '[notify]\nchannel = "none"\n[runtimes]\nenabled = ["claude"]\n')
    monkeypatch.setenv("ORCHESTRA_ROOT", str(root))
    monkeypatch.delenv("ORCHESTRA_CONFIG", raising=False)
    rc = M.main(["up", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "gateway" in out and "cron_beat" in out and "900" in out
    assert not (tmp_path / "data" / "state" / "supervisor.pid").exists()
