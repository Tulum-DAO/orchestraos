"""Seat resolution and the live-seat check (design §6): an agent-takeable item offers `Assign to
<seat>` only when its counterpart is a contact of a client with a `pm_agent` that is online in the
registry. Before a dispatch is sent the seat is checked again; a seat that is no longer live gets
no silent reroute: the same dispatch goes to gm, asking gm to route it, and the effect records
`rerouted`. All data is synthetic."""
import json
import os
import sqlite3
import sys

import pytest

from postcall import effects, ledger, pipeline, seats
from postcall.effects import Effect

from . import _calls
from .test_live_sends import MSG_CLI, _scratch_bus

DECK = "I'll send you the pricing deck by Friday."


@pytest.fixture(autouse=True)
def _verified(monkeypatch):
    _calls.verified(monkeypatch)


@pytest.fixture
def clients(tmp_path, monkeypatch):
    d = tmp_path / "clients"
    monkeypatch.setenv("POSTCALL_CLIENTS_DIR", str(d))

    def write(slug, pm_agent, contacts):
        (d / slug).mkdir(parents=True, exist_ok=True)
        (d / slug / "client.json").write_text(json.dumps({"slug": slug, "pm_agent": pm_agent, "contacts": contacts}))
    return write


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    monkeypatch.setenv("POSTCALL_REGISTRY_FILE", str(path))

    def write(**status):
        path.write_text(json.dumps({"agents": {name: {"name": name, "status": s} for name, s in status.items()}}))
    return write


def _card(tmp_path, agent_takeable=True):
    led, world = ledger.Ledger(str(tmp_path / "l.db")), effects.Recorder()
    it = _calls.item(DECK, agent_takeable=agent_takeable, agent_work="draft the pricing deck")
    call, ex = _calls.call([it])
    pipeline.process(call, ledger=led, extractor=ex, effects=world)
    (card,) = [e for e in world.sent if e.kind == "card"]
    return led, card


def test_resolve_needs_a_client_contact_with_an_online_pm_agent(clients, registry):
    clients("example-widgets", "pm-example", {"primary": {"name": "Dana Reyes", "email": "dana@example.com"}})
    clients("other", "pm-other", [{"name": "Robin Park"}])
    registry(**{"pm-example": "online", "pm-other": "parked"})
    assert seats.resolve("Dana Reyes") == "pm-example"
    assert seats.resolve("  dana   reyes ") == "pm-example"
    assert seats.resolve("Robin Park") is None              # its pm is not live
    assert seats.resolve("Unknown Person") is None
    assert seats.is_live("pm-example") and not seats.is_live("pm-other") and not seats.is_live("pm-none")


def test_agent_takeable_item_with_a_live_seat_offers_assign_first(tmp_path, clients, registry):
    clients("example-widgets", "pm-example", {"primary": {"name": "Dana Reyes"}})
    registry(**{"pm-example": "online"})
    _, card = _card(tmp_path)
    assert card.payload["frozen"][0]["options"][0] == {"action": "assign", "seat": "pm-example",
                                                       "agent_work": "draft the pricing deck"}
    labels = [o["label"] for o in card.payload["questions"][1]["menu"]["options"]]
    assert labels[0] == "Assign to pm-example: draft the pricing deck (suggested)"


@pytest.mark.parametrize("why", ["seat_not_live", "no_client", "not_takeable"])
def test_no_assign_option_without_a_live_resolved_seat(tmp_path, clients, registry, why):
    clients("example-widgets", "pm-example", {"primary": {"name": "Dana Reyes" if why != "no_client" else "X Y"}})
    registry(**{"pm-example": "retired" if why == "seat_not_live" else "online"})
    _, card = _card(tmp_path, agent_takeable=why != "not_takeable")
    assert [o["action"] for o in card.payload["frozen"][0]["options"]] == ["take", "drop"]


def _assigned(tmp_path, clients, registry):
    clients("example-widgets", "pm-example", {"primary": {"name": "Dana Reyes"}})
    registry(**{"pm-example": "online"})
    led, card = _card(tmp_path)
    led.apply_answers(card.effect_id, status="submitted",
                      answers={1: {"option_n": 2, "final": True}, 2: {"option_n": 1, "final": True}})
    (d,) = [e for e in led.effects() if e["kind"] == "dispatch"]
    return led, d["effect_id"]


def test_dispatch_to_a_seat_that_went_offline_goes_to_gm_and_is_recorded_rerouted(tmp_path, clients, registry):
    led, did = _assigned(tmp_path, clients, registry)
    registry(**{"pm-example": "parked"})                    # the seat went away before the send
    world = effects.Recorder()
    led.run_pending(world)
    (sent,) = [e for e in world.sent if e.kind == "dispatch"]
    assert sent.effect_id == did
    assert sent.payload["seat"] == "gm" and sent.payload["rerouted_from"] == "pm-example"
    row = {e["effect_id"]: e for e in led.effects()}[did]
    assert row["state"] == "confirmed" and row["rerouted"] == "pm-example"
    assert json.loads(row["payload_json"])["seat"] == "pm-example"     # the frozen intent is unchanged


def test_dispatch_to_a_live_seat_goes_to_the_seat(tmp_path, clients, registry):
    led, did = _assigned(tmp_path, clients, registry)
    world = effects.Recorder()
    led.run_pending(world)
    (sent,) = [e for e in world.sent if e.kind == "dispatch"]
    assert sent.payload["seat"] == "pm-example" and "rerouted_from" not in sent.payload
    assert {e["effect_id"]: e for e in led.effects()}[did]["rerouted"] is None


def test_a_due_change_after_a_reroute_goes_to_gm(tmp_path, clients, registry):
    led, did = _assigned(tmp_path, clients, registry)
    registry(**{"pm-example": "parked"})
    led.run_pending(effects.Recorder())
    led.record_due_change(did.split(":", 1)[1], "2026-11-30T17:00:00-05:00", "by Monday", "pocket:c2")
    (note,) = [e for e in led.effects() if e["kind"] == "duenote"]
    assert json.loads(note["payload_json"])["to"] == "gm"


def test_live_rerouted_dispatch_is_one_tagged_message_to_gm(tmp_path, request, monkeypatch, clients, registry):
    led, did = _assigned(tmp_path, clients, registry)
    registry(**{"pm-example": "parked"})
    _scratch_bus(os.environ["MSG_DB_PATH"])
    request.node.postcall_allow_argv = [[sys.executable, MSG_CLI]]
    monkeypatch.setenv("POSTCALL_MSG_CLI", MSG_CLI)
    led.run_pending(effects.Live())
    with sqlite3.connect(os.environ["MSG_DB_PATH"]) as c:
        rows = c.execute("SELECT to_agent, subject, body FROM messages").fetchall()
    assert len(rows) == 1
    to, subject, body = rows[0]
    assert to == "gm"
    assert subject == f"[postcall {did}] seat pm-example not live, please route: draft the pricing deck"
    assert "pm-example is not live" in body
