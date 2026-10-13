"""The two stale lines on the "While you were away" card (RFC 0002 section 5.5, gm msg_8a661e3c).

1. /voice-memory read <data>/state/voice-memory.json, a file nothing writes any more: Arturo writes
   <data>/state/arturo/voice-memory.json. The gateway now serves Arturo's file, and the old one only
   when Arturo's is absent.
2. /briefing's "crashed" counted every pane whose detector said `stopped`, which includes service
   panes (python, dashboards, a bare shell). A crash is a registered seat: `crashed` (the registry
   relic rule already applied) or `stopped` in a registered seat's pane. The response shape is
   unchanged, so shipped apps keep decoding it (pinned below)."""
import asyncio
import json
import re
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scripts.watch_gateway as G  # noqa: E402

FLEET = "fleet-bearer-0123456789abcdef-for-tests"
ARTURO = HERE.parent / "services" / "arturo" / "arturo-proxy.py"


@pytest.fixture
def box(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "state" / "arturo").mkdir(parents=True)
    tok = tmp_path / "gateway-token"
    tok.write_text(FLEET)
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    monkeypatch.setattr(G, "ORCH_DIR", data)
    monkeypatch.setattr(G, "TOKEN_FILE", tok)
    return data


def _get(path):
    async def go():
        async with TestClient(TestServer(G.build_app())) as c:
            r = await c.get(path, headers={"Authorization": f"Bearer {FLEET}"})
            return r.status, await r.json()
    return asyncio.run(go())


# ---- /voice-memory serves what Arturo writes ----------------------------------------------------

def test_arturo_writes_voice_memory_under_state_arturo():
    """The writer's path, read from Arturo's source, so a move on either side turns this red."""
    src = ARTURO.read_text()
    assert re.search(r'^ARTURO_STATE = ORCHESTRA_DIR / "state" / "arturo"$', src, re.M)
    assert re.search(r'^VOICE_MEMORY_FILE = ARTURO_STATE / "voice-memory.json"', src, re.M)


def test_voice_memory_serves_arturos_file_when_both_exist(box):
    (box / "state" / "voice-memory.json").write_text(json.dumps(
        {"recent_transcripts": [{"text": "old, from August"}], "notes": []}))
    (box / "state" / "arturo" / "voice-memory.json").write_text(json.dumps(
        {"recent_transcripts": [{"text": "today"}], "notes": ["n"]}))
    status, body = _get("/voice-memory")
    assert status == 200 and body["ok"]
    assert body["recent_transcripts"] == [{"text": "today"}] and body["notes"] == ["n"]


def test_voice_memory_falls_back_to_the_old_file_only_when_arturos_is_absent(box):
    (box / "state" / "voice-memory.json").write_text(json.dumps(
        {"recent_transcripts": [{"text": "old"}], "notes": []}))
    status, body = _get("/voice-memory")
    assert status == 200 and body["ok"] is True
    assert body["recent_transcripts"] == [{"text": "old"}]


def test_voice_memory_with_neither_file_is_empty_not_an_error(box):
    status, body = _get("/voice-memory")
    assert status == 200 and body == {"ok": True, "recent_transcripts": [], "notes": []}


# ---- /briefing: a crash is a registered seat --------------------------------------------------------

def _agent(i, state, unregistered=False):
    return {"id": i, "state": state, "unregistered": unregistered}


def test_briefing_crashed_counts_registered_seats_not_service_panes(box, monkeypatch):
    (box / "registry.json").write_text(json.dumps({"agents": {
        "seat-crashed": {"runtime": "claude"}, "seat-died-in-pane": {"runtime": "codex"},
        "arturo-proxy": {"runtime": "service"}, "ntfy-relay": {"runtime": "service"}}}))
    monkeypatch.setitem(G._agents_cache, "data", [
        _agent("arturo-proxy", "stopped"),                      # a REGISTERED service seat: never a crash
        _agent("ntfy-relay", "crashed"),                        # ... not even with no pane (it may run outside tmux)
        _agent("seat-crashed", "crashed"),                      # registered, no pane, last said alive
        _agent("seat-died-in-pane", "stopped"),                 # registered, agent process gone
        _agent("dashboard", "stopped", unregistered=True),      # a service pane
        _agent("python-proxy", "stopped", unregistered=True),
        _agent("old-relic", "offline"),                         # relic rule already says offline
        _agent("busy", "working"),
    ])
    status, body = _get("/briefing")
    assert status == 200
    assert body["agents_crashed"] == ["seat-crashed", "seat-died-in-pane"]
    assert body["agents_working"] == 1


def test_briefing_response_shape_is_pinned_for_shipped_apps(box, monkeypatch):
    """Keys AND value types, on a non-empty fleet, as the shipped iOS app decodes them."""
    monkeypatch.setitem(G._agents_cache, "data", [
        _agent("w", "working"), _agent("c", "crashed"),
        {"id": "s", "state": "stranded", "stranded_age_s": 42, "stranded_text": "half a line"},
        _agent("t", "stalled")])
    status, body = _get("/briefing")
    assert status == 200 and body["ok"] is True
    assert set(body) == {"ok", "pending", "answered_since", "agents_working", "agents_crashed",
                         "agents_stranded", "agents_stalled"}
    assert isinstance(body["pending"], int) and isinstance(body["answered_since"], list)
    assert body["agents_working"] == 1 and body["agents_crashed"] == ["c"] and body["agents_stalled"] == ["t"]
    assert body["agents_stranded"] == [{"id": "s", "age_s": 42, "text": "half a line"}]
