"""ORCHESTRA_AGENT_ID: every seat's runtime knows its own agent id (message reactions spec, "Agent path").

`orchestra react` (build step 3) names the reacting agent from this variable. It falls back to the tmux session
name when the variable is unset. Today a seat's environment has AGENT_RUNTIME, AGENT_MODEL and PARENT_AGENT_ID, but
not its own id.
Set in three places:
  - spawn-agent.sh: the pane's launch prefix (tmux new-session inherits the SERVER env, so it must be explicit);
  - seat_supervisor.Supervisor._proc_env: the env of the headless CLI the supervisor starts;
  - seat_supervisor.pane_line: the supervisor's own pane line (spawn's env prefix, mirrored).
"""
import importlib.util
import os
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_spec = importlib.util.spec_from_file_location("seat_supervisor_agent_id", HERE / "seat_supervisor.py")
SS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SS)


def test_spawn_agent_pane_prefix_names_the_seat():
    src = (HERE.parent / "spawn-agent.sh").read_text()
    assert ("printf 'ORCHESTRA_DIR=%q ORCH_DIR=%q ORCHESTRA_ROOT=%q ORCHESTRA_AGENT_ID=%q' "
            '"$ORCHESTRA_DIR" "$ORCHESTRA_DIR" "$SCRIPT_DIR" "$agent_id"') in src


def test_proc_env_names_the_seat_and_copies():
    fake = types.SimpleNamespace(env={"PATH": "/bin"}, rot_conf={}, seat="qa-demo")
    env = SS.Supervisor._proc_env(fake)
    assert env["ORCHESTRA_AGENT_ID"] == "qa-demo"
    assert "ORCHESTRA_AGENT_ID" not in fake.env, "the supervisor's own env dict is not mutated"


def test_proc_env_overrides_an_inherited_id_and_keeps_compaction_rule():
    fake = types.SimpleNamespace(env={"ORCHESTRA_AGENT_ID": "someone-else"}, rot_conf={"auto_compact": True},
                                 seat="qa-demo")
    env = SS.Supervisor._proc_env(fake)
    assert env["ORCHESTRA_AGENT_ID"] == "qa-demo"
    assert env["DISABLE_AUTO_COMPACT"] == "1"


def test_pane_line_names_the_seat(monkeypatch):
    monkeypatch.setenv("ORCHESTRA_SEAT_NICE", "10")
    line = SS.pane_line("qa-demo", orch_dir="/tmp/sample-data")
    assert "ORCHESTRA_AGENT_ID=qa-demo " in line
    assert line.endswith("seat_supervisor.py run qa-demo")
