"""Whose call is it, read from contract/agent-call-gate.json: every case in that file run against the voice side's
real rule, services/arturo/agent_call.enabled() (switch on + headless, Dev included), in a temp data dir. The API runs
the same cases (api/src/lib/agentCallGate.test.ts) for the dashboard's "Call <agent>" label.

The engine part is the relay's own: only the GPT-Live relay (stream_relay.py) asks enabled() and puts a call in agent
mode, so a case is the agent's call when enabled() holds AND its engine is "relay". This file only reads the contract."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from services.arturo import agent_call as AC  # noqa: E402

GATE = json.loads((ROOT / "contract" / "agent-call-gate.json").read_text())


def _lay_out(orch: Path, case: dict) -> None:
    seat = case["seat"]
    (orch / "state").mkdir()
    (orch / "state" / "agent-calls.json").write_text(json.dumps({"seats": {seat: {"on": case["switch_on"]}}}))
    seats = {seat: {"headless": True, "runtime": "claude"}} if case["headless"] else {}
    (orch / "state" / "headless-seats.json").write_text(json.dumps({"seats": seats}))
    row = {"runtime": case["registry_runtime"]} if case["registry_runtime"] else {}
    (orch / "registry.json").write_text(json.dumps({"agents": {seat: row}}))
    if case["dev"]:
        (orch / "state" / "headless" / seat).mkdir(parents=True)
        (orch / "state" / "headless" / seat / "dev").write_text("")


@pytest.mark.parametrize("case", GATE["cases"], ids=[c["name"] for c in GATE["cases"]])
def test_every_case_in_the_rule_file(case, tmp_path):
    _lay_out(tmp_path, case)
    got = AC.enabled(case["seat"], orch_dir=tmp_path) and case["engine"] == "relay"
    assert got is case["expected"]


def test_dev_is_reported_not_excluded(tmp_path):
    case = next(c for c in GATE["cases"] if c["dev"] and c["expected"])
    _lay_out(tmp_path, case)
    assert AC.enabled(case["seat"], orch_dir=tmp_path) is True
    assert AC.in_dev(case["seat"], orch_dir=tmp_path) is True


def test_every_case_runs():
    assert len(GATE["cases"]) == 11
