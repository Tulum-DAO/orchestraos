"""The gm results notice (design §6): one `results:<card>` message once every dispatched item on the
card is delivered and has a result, or 48 h after the card's first dispatch (partial). Never twice."""
import datetime as dt
import json

import pytest

from postcall import effects, ledger, pipeline
from postcall.effects import Effect

ASSIGN = [{"action": "assign", "seat": "pm-example", "agent_work": "draft the deck"}, {"action": "take"},
          {"action": "drop"}]
TAKE = [{"action": "take"}, {"action": "drop"}]
CARD = "card:pocket:r1:r0:p1"


def _frozen(key, options, q_n, flagged=False):
    return {"q_n": q_n, "key": key, "options": options, "suggested_n": 1, "flagged": flagged,
            "item": {"owner": "shaw", "text": f"Do {key}", "counterpart": "Dana Reyes", "due_ts": None,
                     "due_source": "none stated", "quote": "q", "citation": "pocket:r1@00:10",
                     "call_title": "Synthetic Vendor Sync"}}


@pytest.fixture
def led(tmp_path):
    led = ledger.Ledger(str(tmp_path / "ledger.db"))
    frozen = [_frozen("k1", ASSIGN, 2), _frozen("k2", ASSIGN, 3), _frozen("k3", TAKE, 4, flagged=True)]
    led.plan_card(Effect(CARD, "card", {"title": "Synthetic Vendor Sync: 3 items", "frozen": frozen}),
                  call_id="pocket:r1", frozen=frozen)
    led.apply_answers(CARD, status="submitted", answers={1: {"option_n": 1, "final": True}})
    led.run_pending(effects.Recorder())
    return led


def _soon(hours=0):
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)


def _notices(led):
    return [e for e in led.effects() if e["kind"] == "results"]


def test_no_notice_while_results_are_missing(led):
    led.record_result(led.commitment_of("k1"), "deck drafted")
    assert pipeline.results_due(led, _soon()) == []
    assert _notices(led) == []


def test_one_notice_when_every_dispatch_has_a_result(led):
    led.record_result(led.commitment_of("k1"), "deck drafted")
    led.record_result(led.commitment_of("k2"), "deck drafted")
    assert pipeline.results_due(led, _soon()) == [f"results:{CARD}"]
    n, = _notices(led)
    p = json.loads(n["payload_json"])
    assert p["to"] == "gm"
    assert p["subject"] == "Post-call results ready: Synthetic Vendor Sync"
    assert "2/2 done" in p["body"] and "unreviewed 1" in p["body"]
    world = effects.Recorder()
    led.run_pending(world)
    assert [e.effect_id for e in world.sent] == [f"results:{CARD}"]


def test_partial_notice_after_48_hours(led):
    led.record_result(led.commitment_of("k1"), "deck drafted")
    assert pipeline.results_due(led, _soon(47)) == []
    assert pipeline.results_due(led, _soon(49)) == [f"results:{CARD}"]
    p = json.loads(_notices(led)[0]["payload_json"])
    assert "1/2 done" in p["body"] and "partial" in p["body"]


def test_notice_is_never_sent_twice(led):
    pipeline.results_due(led, _soon(49))
    world = effects.Recorder()
    led.run_pending(world)
    led.record_result(led.commitment_of("k1"), "deck drafted")
    led.record_result(led.commitment_of("k2"), "deck drafted")
    assert pipeline.results_due(led, _soon(50)) == []
    led.run_pending(world)
    assert len(_notices(led)) == 1
    assert [e.kind for e in world.sent] == ["results"]


def test_a_card_with_nothing_dispatched_gets_no_notice(tmp_path):
    led = ledger.Ledger(str(tmp_path / "l.db"))
    frozen = [_frozen("k1", TAKE, 2)]
    led.plan_card(Effect(CARD, "card", {"title": "T", "frozen": frozen}), call_id="pocket:r1", frozen=frozen)
    led.apply_answers(CARD, status="submitted", answers={1: {"option_n": 1, "final": True}})
    led.run_pending(effects.Recorder())
    assert pipeline.results_due(led, _soon(100)) == []
