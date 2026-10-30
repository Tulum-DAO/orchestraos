"""Test seats (row test: true) are quiet: the router never pages about one being down or recovered and never
escalates its held rows; every other seat is unchanged. The gateway's /agents leaves out a test seat with no
session. Fakes only: no store, no tmux, no Telegram."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))


@pytest.fixture
def MR(monkeypatch):
    spec = importlib.util.spec_from_file_location("mr_test_seats", HERE / "message-router.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._sent = []
    monkeypatch.setattr(m, "log", lambda *a, **k: None)
    monkeypatch.setattr(m, "telegram", lambda text, *a, **k: m._sent.append(("telegram", text)))
    monkeypatch.setattr(m, "_send_page", lambda to, subject, body, *a, **k: m._sent.append((to, subject)))
    monkeypatch.setattr(m, "_target_rotated_within", lambda *a, **k: False)
    m.set_test_seats({"qa-demo": {"test": True, "tmux_session": "qa-demo"},
                      "dev-demo": {"tier": "T2"}})
    return m


def test_test_seats_come_from_the_registry_rows(MR):
    assert MR.is_test_seat("qa-demo") and not MR.is_test_seat("dev-demo") and not MR.is_test_seat("")
    assert MR.set_test_seats(None) == set() and not MR.is_test_seat("qa-demo")


def test_no_down_page_for_a_test_seat(MR, monkeypatch):
    monkeypatch.setattr(MR, "escalation_targets", lambda seat: pytest.fail("escalation asked for a test seat"))
    down = {}
    assert MR.page_seat_down(down, "qa-demo", 2, 1000.0, 900.0, agent="qa-demo") is False
    assert MR._sent == []


def test_no_recovered_page_for_a_test_seat(MR):
    MR.clear_seat_down({"qa-demo": {"owner_paged_at": 1, "first_seen": 1}}, "qa-demo", now=100.0)
    assert MR._sent == []
    MR.clear_seat_down({"dev-demo": {"owner_paged_at": 1, "first_seen": 1}}, "dev-demo", now=100.0)
    assert MR._sent and "[RECOVERED] dev-demo" in MR._sent[0][1]


def test_held_rows_of_a_test_seat_never_escalate(MR, monkeypatch):
    old = {"id": "msg_sample1", "from_agent": "pm-demo", "to_agent": "qa-demo", "subject": "Sample",
           "priority": "medium", "created_at": "2020-01-01T00:00:00"}
    hold = {}
    MR.note_hold(hold, "qa-demo", old, "not-idle", 2e9)
    assert MR._sent == []
    other = dict(old, id="msg_sample2", to_agent="dev-demo")
    MR.note_hold(hold, "dev-demo", other, "not-idle", 2e9)
    assert any(kind == "telegram" for kind, _ in MR._sent)      # a real seat still escalates


def test_gateway_agents_skip_a_sessionless_test_seat(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCH_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "path", [str(HERE), str(ROOT)] + list(sys.path))
    spec = importlib.util.spec_from_file_location("watch_gateway_test_seats", HERE / "watch_gateway.py")
    G = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(G)
    import json
    (tmp_path / "registry.json").write_text(json.dumps({"agents": {
        "qa-demo": {"test": True, "tmux_session": "qa-demo", "always_on": True},
        "dev-demo": {"tmux_session": "dev-demo", "always_on": True}}}))
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(G, "_agent_status", lambda: types.SimpleNamespace(get_agent_status=lambda s: {}))
    monkeypatch.setattr(G, "_tmux_session_names", lambda: [])
    monkeypatch.setattr(G, "_live_voice_call", lambda: None)
    monkeypatch.setattr(G, "_reap_absent_instances", lambda *a: None)
    for name in ("_stamp_recency_and_subagents", "_stamp_last_user_message", "_stamp_lineage", "_stamp_chips"):
        monkeypatch.setattr(G, name, lambda *a, **k: None)
    ids = [r["id"] for r in G.compute_agents()]
    assert ids == ["dev-demo"]


def test_gateway_agents_skip_an_old_generation_of_a_test_lineage(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCH_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "path", [str(HERE), str(ROOT)] + list(sys.path))
    spec = importlib.util.spec_from_file_location("watch_gateway_test_lineage", HERE / "watch_gateway.py")
    G = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(G)
    import json
    (tmp_path / "registry.json").write_text(json.dumps({"agents": {
        "qa-demo": {"test": True, "tmux_session": "qa-demo", "lineage_root": "qa-demo"},
        "qa-demo-gen1": {"tmux_session": "qa-demo-gen1", "lineage_root": "qa-demo", "status": "retired"},
        "dev-demo-gen1": {"tmux_session": "dev-demo-gen1", "lineage_root": "dev-demo", "status": "parked"}}}))
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(G, "_agent_status", lambda: types.SimpleNamespace(get_agent_status=lambda s: {}))
    monkeypatch.setattr(G, "_tmux_session_names", lambda: [])
    monkeypatch.setattr(G, "_live_voice_call", lambda: None)
    monkeypatch.setattr(G, "_reap_absent_instances", lambda *a: None)
    for name in ("_stamp_recency_and_subagents", "_stamp_last_user_message", "_stamp_lineage", "_stamp_chips"):
        monkeypatch.setattr(G, name, lambda *a, **k: None)
    assert [r["id"] for r in G.compute_agents()] == ["dev-demo-gen1"]   # a real lineage's leftover stays visible
