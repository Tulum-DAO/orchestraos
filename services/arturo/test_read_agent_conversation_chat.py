"""read_agent_conversation reads the agent's CHAT, the thread the dashboard shows (Shaw 2026-11-25: "you can't read
what the GM is saying"). It used to read only pending msg_store inbox rows, so in a call it answered "Inbox for gm:
0 pending messages" while gm had been talking to Shaw all morning.

The chat goes only to a verified fleet/owner caller (fenced or not: the fence is about what a turn may DO); anyone
else keeps the inbox summary. An unreadable API falls back to the inbox, never to an error.
"""
import sys
import types

import pytest

from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture (ARTURO_STATE in tmp)

ITEMS = [
    {"kind": "text", "role": "user", "text": "is the dream fix in?", "ts": "2026-11-25T17:10:00Z", "audience": "user"},
    {"kind": "text", "role": "assistant", "text": "Yes, merged at 06:09.", "ts": "2026-11-25T17:10:30Z",
     "audience": "user"},
    {"kind": "text", "role": "user", "text": "[mail]", "ts": "2026-11-25T17:11:00Z", "audience": "agent",
     "machine": "relay"},
]


@pytest.fixture()
def store(monkeypatch):
    class _Store:
        def inbox(self, agent, tenant_id=None):
            return [{"status": "pending", "priority": "high", "from_agent": "dev-x", "subject": "a task"}]
    monkeypatch.setitem(sys.modules, "msg_store", types.SimpleNamespace(MessageStore=_Store))


def _run(P, principal, fetch, monkeypatch, fenced=False):
    monkeypatch.setattr(P._thread_ctx, "fetch_transcript", fetch)
    turn = {"principal": principal, "voice": True, "fenced": fenced}
    return P._as_turn(turn, P.execute_tool, "read_agent_conversation", {"agent_id": "gm"})


def test_a_fleet_call_reads_the_agents_chat_stamped(P, store, monkeypatch):
    asked = []
    out = _run(P, "fleet", lambda agent, limit: asked.append(agent) or ITEMS, monkeypatch)
    assert asked == ["gm"]
    assert "is the dream fix in?" in out and "Yes, merged at 06:09." in out
    assert "[2026-11-25T17:10:00Z" in out                     # every line stamped
    assert "[mail]" not in out                                # machine turns are not the chat
    assert "1 pending" in out                                 # the inbox count still rides along


def test_a_fenced_fleet_turn_still_reads_the_chat(P, store, monkeypatch):
    out = _run(P, "fleet", lambda agent, limit: ITEMS, monkeypatch, fenced=True)
    assert "Yes, merged at 06:09." in out


def test_an_unverified_caller_gets_the_inbox_not_the_chat(P, store, monkeypatch):
    asked = []
    out = _run(P, "device:dev_voice", lambda agent, limit: asked.append(agent) or ITEMS, monkeypatch)
    assert asked == []                                        # not fetched at all
    assert "Inbox for gm: 1 pending" in out and "dream fix" not in out and "could not be read" not in out


def test_an_unreadable_api_falls_back_to_the_inbox(P, store, monkeypatch):
    def down(agent, limit):
        raise OSError("connection refused")
    out = _run(P, "fleet", down, monkeypatch)
    assert "Inbox for gm: 1 pending" in out and not out.startswith("Failed")
    assert "chat could not be read" in out
