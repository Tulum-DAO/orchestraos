"""Re-ingest of a carded call (design §1 "Revisions"): a changed transcript bumps the call's revision
and files `card:<call>:r<rev>:p<n>` holding ONLY the items not already asked. The old card's frozen
items never change, so an answer on it can never act on an item it did not show."""
import json

import pytest

from postcall import effects, extract, ledger, pipeline
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


def _failing(_c, existing=None):
    return extract.Extraction(ok=False, raw=None, error="claude exited 1")


def _bad_label(items):
    def ex(_c, existing=None):
        return extract.Extraction(ok=True, raw={"shaw_label": "SPEAKER_01", "shaw_label_evidence": "This is Shaw.",
                                                "items": list(items)})
    return ex


@pytest.mark.parametrize("fail", ["extractor", "attribution"])
def test_a_reingest_that_fails_once_still_cards_its_new_items_at_the_next_revision(led, fail):
    """Review round 2, B1: a failure path stores the new hash; the retry must still bump the revision."""
    world = effects.Recorder()
    call1, ex1 = _calls.call(FIRST, content_hash="h1")
    assert pipeline.process(call1, ledger=led, extractor=ex1, effects=world).status == "carded"

    extra = _calls.item(CONTRACT, deliverable="signed contract", timeframe="by Monday")
    call2, ex2 = _calls.call(FIRST + [extra], content_hash="h2")
    broken = _failing if fail == "extractor" else _bad_label(FIRST + [extra])
    assert pipeline.process(call2, ledger=led, extractor=broken, effects=world).status == "failed_retry"

    assert pipeline.process(call2, ledger=led, extractor=ex2, effects=world).status == "carded"
    cards = [e for e in world.sent if e.kind == "card"]
    assert [e.effect_id for e in cards] == ["card:pocket:c1:r0:p1", "card:pocket:c1:r1:p1"]
    assert [f["item"]["quote"] for f in cards[1].payload["frozen"]] == [CONTRACT]


def test_a_crash_between_pages_never_leaves_a_half_planned_card(led, monkeypatch):
    """All pages of one card revision are planned in one transaction, so a retry is never refused."""
    world = effects.Recorder()
    items = [_calls.item(f"I will send you report number {n} with the figures by Friday.",
                         deliverable=f"report {n}") for n in range(1, 22)]
    call1, ex1 = _calls.call(items, content_hash="h1")
    real = ledger.Ledger.plan
    planned = []

    def plan(self, effect, **kw):
        if effect.kind == "card":
            planned.append(effect.effect_id)
            if effect.effect_id.endswith(":p2"):
                raise RuntimeError("crash between pages")
        return real(self, effect, **kw)

    monkeypatch.setattr(ledger.Ledger, "plan", plan)
    with pytest.raises(RuntimeError):
        pipeline.process(call1, ledger=led, extractor=ex1, effects=world)
    monkeypatch.setattr(ledger.Ledger, "plan", real)
    assert [e for e in led.effects() if e["kind"] == "card"] == []
    assert pipeline.process(call1, ledger=led, extractor=ex1, effects=world).status == "carded"
    assert sorted(e.effect_id for e in world.sent if e.kind == "card") == ["card:pocket:c1:r0:p1",
                                                                          "card:pocket:c1:r0:p2"]
