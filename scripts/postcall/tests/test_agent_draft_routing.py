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
