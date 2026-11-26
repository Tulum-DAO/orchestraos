"""Retire at 14 days (gm ruling §9.6, design §4): a filed card still pending 14 days after its
Decide-by is retired by its author with reason "no answer". Its undecided items stay in the ledger as
`unreviewed`; nothing is created from them. Decide-by rides on the card effect's payload."""
import datetime as dt
import json
import os
import sqlite3
import sys

import pytest

from postcall import effects, ledger, pipeline
from postcall.effects import Effect

from . import _calls

DECK = "I'll send you the pricing deck by Friday."
CREDS = "I will share the API credentials with you tomorrow morning."


@pytest.fixture(autouse=True)
def _verified(monkeypatch):
    _calls.verified(monkeypatch)


def _filed(tmp_path):
    led, world = ledger.Ledger(str(tmp_path / "l.db")), effects.Recorder()
    call, ex = _calls.call([_calls.item(DECK), _calls.item(CREDS, owner="participant", deliverable="api credentials",
                                                           text="Dana shares the API credentials",
                                                           timeframe="tomorrow morning")])
    pipeline.process(call, ledger=led, extractor=ex, effects=world)
    (card,) = [e for e in world.sent if e.kind == "card"]
    return led, world, card


def test_card_payload_carries_decide_by(tmp_path):
    _, _, card = _filed(tmp_path)
    # Earliest due: Dana's "tomorrow morning" = Wed 25 Nov 09:00 Local; decide two hours before.
    assert card.payload["decide_by"] == "2026-11-25T07:00:00-05:00"
    assert card.payload["summary"].startswith("Decide by: Wed 25 Nov 07:00 Local")


def test_pending_card_is_retired_14_days_after_decide_by_once(tmp_path):
    led, world, card = _filed(tmp_path)
    decide_by = dt.datetime.fromisoformat(card.payload["decide_by"])
    assert pipeline.retire_stale(led, world, decide_by + dt.timedelta(days=14, minutes=-1)) == []
    assert [e.kind for e in world.sent] == ["card"]

    assert pipeline.retire_stale(led, world, decide_by + dt.timedelta(days=14, minutes=1)) == [f"retire:{card.effect_id}"]
    (ret,) = [e for e in world.sent if e.kind == "retire"]
    assert ret.effect_id == f"retire:{card.effect_id}"
    assert ret.payload == {"questionnaire": "ext-1", "reason": "no answer", "card": card.effect_id}
    assert {led.item_state(f["key"]) for f in card.payload["frozen"]} == {"unreviewed"}
    assert [e for e in led.effects() if e["kind"] in ("task", "dispatch")] == []

    assert pipeline.retire_stale(led, world, decide_by + dt.timedelta(days=30)) == []
    assert len([e for e in world.sent if e.kind == "retire"]) == 1


def test_an_answered_card_is_never_retired(tmp_path):
    led, _, card = _filed(tmp_path)

    class Answered(effects.Recorder):
        def card_answers(self, external_id):
            return "submitted", {1: {"option_n": 3, "final": True}}

    later = dt.datetime.fromisoformat(card.payload["decide_by"]) + dt.timedelta(days=20)
    assert pipeline.retire_stale(led, Answered(), later) == []


def test_live_retire_runs_the_approval_retire_verb(monkeypatch, tmp_path):
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "qnr_abc\n", ""

    monkeypatch.setattr(effects._subprocess, "run", lambda argv, *a, **kw: calls.append(argv) or Done())
    live = effects.Live()
    eff = Effect("retire:card:pocket:c1:r0:p1", "retire", {"questionnaire": "qnr_abc", "reason": "no answer"})
    assert live.send(eff) == "retire-qnr_abc"
    assert calls == [[sys.executable, live.cli, "retire", "--id", "qnr_abc", "--from", "postcall-dev",
                      "--reason", "no answer"]]
    with sqlite3.connect(os.environ["APPROVAL_DB_PATH"]) as c:
        c.execute("CREATE TABLE questionnaires (id TEXT PRIMARY KEY, status TEXT)")
        c.execute("INSERT INTO questionnaires VALUES ('qnr_abc', 'pending')")
    assert live.find(eff) is None
    with sqlite3.connect(os.environ["APPROVAL_DB_PATH"]) as c:
        c.execute("UPDATE questionnaires SET status='retired'")
    assert live.find(eff) == "retire-qnr_abc"


def test_shadow_records_the_retire(tmp_path):
    shadow = effects.Shadow(str(tmp_path / "shadow.jsonl"))
    eff = Effect("retire:card:x:r0:p1", "retire", {"questionnaire": "shadow-1", "reason": "no answer"})
    ext = shadow.send(eff)
    assert shadow.find(eff) == ext
    assert json.loads((tmp_path / "shadow.jsonl").read_text())["kind"] == "retire"


def test_the_sweep_retires_stale_cards_using_its_injected_now(tmp_path, monkeypatch):
    from postcall import sweep
    (tmp_path / "pocket").mkdir()
    monkeypatch.setenv("POCKET_ROOT", str(tmp_path / "pocket"))
    led = ledger.Ledger(effects.ledger_path("test"))
    frozen = [{"q_n": 2, "key": "k1", "options": [{"action": "take"}], "suggested_n": 1, "flagged": False}]
    led.plan_card(Effect("card:pocket:c9:r0:p1", "card", {"title": "t", "decide_by": "2026-11-25T07:00:00-05:00"}),
                  call_id="pocket:c9", frozen=frozen)
    world = effects.Recorder()
    led.run_pending(world)

    def no_calls(call):
        raise AssertionError("no calls")

    at = dt.datetime(2026, 12, 9, 13, 0, tzinfo=dt.timezone.utc).timestamp()      # 14 d + 1 h after
    out = sweep.run_once(now=at - 2 * 3600, extractor=no_calls, adapter=world)
    assert out["retired"] == [] and [e.kind for e in world.sent] == ["card"]
    out = sweep.run_once(now=at, extractor=no_calls, adapter=world)
    assert out["retired"] == ["retire:card:pocket:c9:r0:p1"]
    assert [e.kind for e in world.sent] == ["card", "retire", "results"]     # gm hears about the unreviewed item
    assert led.item_state("k1") == "unreviewed"
