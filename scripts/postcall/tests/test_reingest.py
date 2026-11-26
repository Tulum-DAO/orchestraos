"""Re-ingest of a carded call (design §1 "Revisions"): a changed transcript bumps the call's revision
and files `card:<call>:r<rev>:p<n>` holding ONLY the items not already asked. The old card's frozen
items never change, so an answer on it can never act on an item it did not show."""
import json

import pytest

from postcall import effects, ledger, pipeline
from postcall.effects import Effect

from . import _calls

DECK = "I'll send you the pricing deck by Friday."
CREDS = "I will share the API credentials with you tomorrow morning."
CONTRACT = "I will also send you the signed contract by Monday."


@pytest.fixture(autouse=True)
def _verified(monkeypatch):
    _calls.verified(monkeypatch)


@pytest.fixture
def led(tmp_path):
    return ledger.Ledger(str(tmp_path / "ledger.db"))


def _card_items(led, card_id):
    import sqlite3
    with sqlite3.connect(led.path) as c:
        return c.execute("SELECT q_n, key FROM card_items WHERE card_id=? ORDER BY q_n", (card_id,)).fetchall()


FIRST = [_calls.item(DECK), _calls.item(CREDS, owner="participant", deliverable="api credentials",
                                        text="Dana shares the API credentials", timeframe="tomorrow morning")]


def test_reingest_with_a_new_item_files_a_new_revision_card_with_only_that_item(led):
    world = effects.Recorder()
    call1, ex1 = _calls.call(FIRST, content_hash="h1")
    assert pipeline.process(call1, ledger=led, extractor=ex1, effects=world).status == "carded"
    old_id = "card:pocket:c1:r0:p1"
    old_items = _card_items(led, old_id)
    assert len(old_items) == 2

    extra = _calls.item(CONTRACT, deliverable="signed contract", timeframe="by Monday")
    call2, ex2 = _calls.call(FIRST + [extra], content_hash="h2")
    pipeline.process(call2, ledger=led, extractor=ex2, effects=world)

    cards = [e for e in world.sent if e.kind == "card"]
    assert [e.effect_id for e in cards] == [old_id, "card:pocket:c1:r1:p1"]
    new = cards[1]
    assert [f["item"]["quote"] for f in new.payload["frozen"]] == [CONTRACT]
    assert _card_items(led, old_id) == old_items                       # the old card is untouched
    assert led.call_row("pocket:c1")["revision"] == 1

    new_key = new.payload["frozen"][0]["key"]
    led.apply_answers(old_id, status="submitted", answers={1: {"option_n": 1, "final": True}})
    tasks = [json.loads(e["payload_json"]) for e in led.effects() if e["kind"] == "task"]
    assert len(tasks) == 2 and new_key not in {t["key"] for t in tasks}
    assert led.decision(new_key) is None


def test_reingest_with_no_new_items_files_no_card(led):
    world = effects.Recorder()
    call1, ex1 = _calls.call(FIRST, content_hash="h1")
    pipeline.process(call1, ledger=led, extractor=ex1, effects=world)
    call2, ex2 = _calls.call(FIRST, content_hash="h2")
    pipeline.process(call2, ledger=led, extractor=ex2, effects=world)
    assert [e.effect_id for e in world.sent if e.kind == "card"] == ["card:pocket:c1:r0:p1"]
    assert led.call_row("pocket:c1")["content_hash"] == "h2"


def test_plan_card_refuses_to_add_items_to_a_card_that_has_items(led):
    f1 = {"q_n": 2, "key": "k1", "options": [{"action": "take"}], "suggested_n": 1, "flagged": False}
    f2 = {"q_n": 3, "key": "k2", "options": [{"action": "take"}], "suggested_n": 1, "flagged": False}
    led.plan_card(Effect("card:x:r0:p1", "card", {}), call_id="x", frozen=[f1])
    with pytest.raises(ValueError, match="already has items"):
        led.plan_card(Effect("card:x:r0:p1", "card", {}), call_id="x", frozen=[f1, f2])
    assert _card_items(led, "card:x:r0:p1") == [(2, "k1")]
