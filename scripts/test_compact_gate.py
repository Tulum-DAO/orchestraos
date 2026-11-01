"""The /compact rule, read from contract/compact-gate.json: every case in that file run against the gateway's
_compact_allowed, with a fixture registry in a temp data dir and the headless lookup stubbed (no live seat)."""
import importlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
G = importlib.import_module("watch_gateway")
GATE = json.loads(G.COMPACT_GATE.read_text())


@pytest.mark.parametrize("case", GATE["cases"], ids=[c["name"] for c in GATE["cases"]])
def test_every_case_in_the_rule_file(case, tmp_path, monkeypatch):
    seat, row = case["seat"], case["registry"]
    # the seat's registry row is keyed by seat; no row at all is an empty registry
    (tmp_path / "registry.json").write_text(json.dumps({"agents": {seat: row} if row is not None else {}}))
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    # the gateway is asked by tmux session (the row's, else the seat id), and its headless lookup answers with the
    # seat when it is switched on headless and not in Dev (headless_switch.headless_seat, include_dev off)
    session = (row or {}).get("tmux_session") or seat
    hl = seat if case["headless"] and not case["dev"] else None
    assert G._compact_allowed(session, hl) is case["expected"]


def test_all_twelve_cases_run():
    assert len(GATE["cases"]) == 12


def test_the_rule_file_holds_todays_rule():
    assert GATE["needs_headless"] is True and GATE["needs_test_seat"] is True


def test_an_unreadable_rule_refuses(tmp_path, monkeypatch):
    (tmp_path / "registry.json").write_text(json.dumps({"agents": {"qa-demo": {"test": True}}}))
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    for content in (None, "not json", "[]"):
        gate = tmp_path / "gate.json"
        if content is None:
            gate.unlink(missing_ok=True)
        else:
            gate.write_text(content)
        monkeypatch.setattr(G, "COMPACT_GATE", gate)
        G._compact_gate_cache.update(mtime=None, doc=None)
        assert G._compact_allowed("qa-demo", "qa-demo") is False, content


def test_a_changed_rule_is_read_again(tmp_path, monkeypatch):
    (tmp_path / "registry.json").write_text(json.dumps({"agents": {"pm-demo": {}}}))
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"needs_headless": True, "needs_test_seat": True, "cases": []}))
    monkeypatch.setattr(G, "COMPACT_GATE", gate)
    G._compact_gate_cache.update(mtime=None, doc=None)
    assert G._compact_allowed("pm-demo", "pm-demo") is False
    import os
    gate.write_text(json.dumps({"needs_headless": True, "needs_test_seat": False, "cases": []}))
    os.utime(gate, (1_900_000_000, 1_900_000_000))
    assert G._compact_allowed("pm-demo", "pm-demo") is True, "a later go is a one-line change in the file"
