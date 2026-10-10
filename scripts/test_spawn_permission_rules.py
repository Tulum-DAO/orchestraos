"""Spawn-time permission allow-rules (scripts/spawn_permission_rules.py). gm msg_75776be5: the rule set is
derived from the install's own data dir and checkout only; it used to default to one operator's private
checkout path, so every seat on every install got allow rules for that path instead of its data dir."""
import json
import os
import pathlib
import re

import pytest

import spawn_permission_rules as spr

ROOT = pathlib.Path(__file__).resolve().parents[1]
PERSONAL = re.compile(r"/(home|Users)/[^/]+/scripts/agent-orchestra")


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for k in ("ORCHESTRA_DIR", "ORCH_DIR", "BG_GREEN_ROOT"):
        monkeypatch.delenv(k, raising=False)


def test_without_env_the_rules_name_the_product_default_data_dir():
    from orchestra_cli.settings import DEFAULT_DATA_DIR
    data = os.path.expanduser(DEFAULT_DATA_DIR).rstrip("/")
    rules = spr.build_allow_rules("/tmp/seat-cwd")
    assert f"Write(/{data}/state/**)" in rules and f"Edit(/{data}/.workspace/**)" in rules


def test_the_rules_follow_ORCHESTRA_DIR(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    rules = spr.build_allow_rules("/tmp/seat-cwd")
    assert f"Write(/{tmp_path}/data/state/**)" in rules


@pytest.mark.parametrize("env", [{}, {"ORCHESTRA_DIR": "/srv/orch-data"}, {"ORCH_DIR": "/srv/old-name"}])
def test_no_allow_rule_ever_names_a_personal_checkout_path(monkeypatch, tmp_path, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("BG_GREEN_ROOT", "x")          # the green variant carries a hook too
    path = spr.write_settings("seat-x", "/tmp/seat-cwd", out_dir=str(tmp_path))
    text = pathlib.Path(path).read_text()
    assert not PERSONAL.search(text), text


def test_only_write_tools_never_a_shell_or_network_tool():
    rules = spr.build_allow_rules("/tmp/seat-cwd", orchestra_dir="/srv/orch-data")
    tools = {r.split("(")[0] for r in rules}
    assert tools == set(spr.WRITE_TOOLS)


def test_the_green_hook_runs_the_checkouts_script_even_with_an_empty_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "empty-data"))
    monkeypatch.setenv("BG_GREEN_ROOT", "x")
    path = spr.write_settings("green-x", "/tmp/seat-cwd", out_dir=str(tmp_path))
    cmd = json.loads(pathlib.Path(path).read_text())["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    script = cmd.split(" ", 1)[1]
    assert pathlib.Path(script).is_file() and not script.startswith(str(tmp_path)), script


def test_spawn_agent_passes_its_data_dir():
    src = (ROOT / "spawn-agent.sh").read_text()
    line = next(l for l in src.splitlines() if "spawn_permission_rules.py" in l and "python3" in l)
    assert '--orchestra-dir "$ORCHESTRA_DIR"' in line, line
