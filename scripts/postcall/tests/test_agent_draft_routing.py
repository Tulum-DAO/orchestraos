"""gm msg_feff515d (1): agent-takeable work is offered even when no client-contact seat resolves.
The draft goes to gm to route (sending stays human); the dispatch says so."""
import json

import pytest

from postcall import effects, ledger, pipeline
from postcall.effects import Effect

from . import _calls

DECK = "I'll send you the pricing deck by Friday."


@pytest.fixture(autouse=True)
def _verified(monkeypatch):
    _calls.verified(monkeypatch)


def test_agent_work_with_no_resolved_seat_is_offered_to_gm_for_routing(tmp_path):
    led, world = ledger.Ledger(str(tmp_path / "l.db")), effects.Recorder()
    call, ex = _calls.call([_calls.item(DECK, agent_takeable=True, agent_work="draft the pricing deck email")])
    assert pipeline.process(call, ledger=led, extractor=ex, effects=world).status == "carded"
    (f,) = world.sent[0].payload["frozen"]
    assert f["options"][0] == {"action": "assign", "seat": "gm", "agent_work": "draft the pricing deck email",
                               "route": True}
    led.apply_answers(world.sent[0].effect_id, status="submitted", answers={2: {"option_n": 1, "final": True}})
    (d,) = [json.loads(e["payload_json"]) for e in led.effects() if e["kind"] == "dispatch"]
    assert d["seat"] == "gm" and d["route"] is True


def test_no_agent_work_means_no_assign_option(tmp_path):
    led, world = ledger.Ledger(str(tmp_path / "l.db")), effects.Recorder()
    call, ex = _calls.call([_calls.item(DECK, agent_takeable=False, agent_work="")])
    pipeline.process(call, ledger=led, extractor=ex, effects=world)
    (f,) = world.sent[0].payload["frozen"]
    assert [o["action"] for o in f["options"]] == ["take", "drop"]


def test_a_routed_dispatch_asks_gm_to_pick_the_seat(monkeypatch):
    sent = {}
    live = effects.Live()
    monkeypatch.setattr(live, "_message", lambda eff, to, kind, subject, body: sent.update(
        to=to, subject=subject, body=body) or "msg_x")
    item = {"text": "Send Dana the pricing deck", "counterpart": "Dana Reyes", "due_source": "by Friday",
            "quote": DECK, "citation": "pocket:c1@01:00"}
    live._send_dispatch(Effect("dispatch:cmt_a", "dispatch", {"cmt": "cmt_a", "seat": "gm", "route": True,
                                                               "agent_work": "draft the pricing deck email",
                                                               "decision_src": "card:x#2", "item": item}))
    assert sent["to"] == "gm"
    assert sent["subject"].startswith("please route: draft the pricing deck email")
    assert sent["body"].splitlines()[0].startswith("No client seat resolved for this work: please pick")
    assert "never send anything as Shaw" in sent["body"]


def test_a_chase_item_with_agent_work_and_no_seat_routes_its_nudge_through_gm(tmp_path):
    led, world = ledger.Ledger(str(tmp_path / "l.db")), effects.Recorder()
    theirs = _calls.item("I will share the API credentials with you tomorrow morning.", owner="participant",
                         deliverable="api credentials", text="Dana shares the API credentials",
                         timeframe="tomorrow morning", agent_takeable=True, agent_work="draft a reminder")
    call, ex = _calls.call([theirs])
    pipeline.process(call, ledger=led, extractor=ex, effects=world)
    (f,) = world.sent[0].payload["frozen"]
    assign = [o for o in f["options"] if o["action"] == "assign"]
    assert assign == [{"action": "assign", "seat": "gm", "agent_work": "draft a follow-up nudge", "route": True}]
    labels = [o["label"] for o in world.sent[0].payload["questions"][1]["menu"]["options"]]
    assert any(lab.startswith("Assign to gm to route: draft a follow-up nudge") for lab in labels)


def test_a_routed_dispatch_is_sent_once_and_reconciled_on_replay(tmp_path):
    led, world = ledger.Ledger(str(tmp_path / "l.db")), effects.Recorder()
    call, ex = _calls.call([_calls.item(DECK, agent_takeable=True, agent_work="draft the pricing deck email")])
    pipeline.process(call, ledger=led, extractor=ex, effects=world)
    led.apply_answers(world.sent[0].effect_id, status="submitted", answers={2: {"option_n": 1, "final": True}})
    led.run_pending(world)
    led.run_pending(world)
    pipeline.process(call, ledger=led, extractor=ex, effects=world)
    sent = [e for e in world.sent if e.kind == "dispatch"]
    assert len(sent) == 1 and sent[0].payload["route"] is True and sent[0].payload["seat"] == "gm"


def test_a_routed_dispatch_accepted_outside_but_not_confirmed_is_reconciled_not_resent(tmp_path, monkeypatch):
    path = str(tmp_path / "l.db")
    led, world = ledger.Ledger(path), effects.Recorder()
    call, ex = _calls.call([_calls.item(DECK, agent_takeable=True, agent_work="draft the pricing deck email")])
    pipeline.process(call, ledger=led, extractor=ex, effects=world)
    led.apply_answers(world.sent[0].effect_id, status="submitted", answers={2: {"option_n": 1, "final": True}})
    real = ledger.Ledger._set_effect

    def crash_on_confirm(self, effect_id, state, external_id=None):
        if effect_id.startswith("dispatch:") and state == "confirmed":
            raise SystemExit("crash after the send, before the confirm")
        return real(self, effect_id, state, external_id)
    monkeypatch.setattr(ledger.Ledger, "_set_effect", crash_on_confirm)
    with pytest.raises(SystemExit):
        led.run_pending(world)
    monkeypatch.setattr(ledger.Ledger, "_set_effect", real)
    assert len([e for e in world.sent if e.kind == "dispatch"]) == 1

    ledger.Ledger(path).run_pending(world)                       # a fresh sweep after the crash
    assert len([e for e in world.sent if e.kind == "dispatch"]) == 1
    (d,) = [e for e in ledger.Ledger(path).effects() if e["kind"] == "dispatch"]
    assert d["state"] == "confirmed" and d["external_id"]
