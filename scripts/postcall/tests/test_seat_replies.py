"""Seat results (design §6): the sweep reads replies addressed to postcall-dev whose subject carries
`[postcall dispatch:<cmt>]` (read-only, from the message DB) and records one result per commitment.
The gm notice reports done, unreviewed and due_pending_patch counts; a card with nothing dispatched
but unreviewed items still gets one notice listing them."""
import datetime as dt
import json
import os
import sqlite3

import pytest

from postcall import effects, ledger, pipeline, sweep
from postcall.effects import Effect

from .test_live_sends import _scratch_bus

CARD = "card:pocket:r1:r0:p1"
NOW = dt.datetime(2026, 11, 26, 0, 0, tzinfo=dt.timezone.utc).timestamp()
ASSIGN = [{"action": "assign", "seat": "pm-example", "agent_work": "draft the pricing deck"}, {"action": "take"},
          {"action": "drop"}]
TAKE = [{"action": "take"}, {"action": "drop"}]


def _frozen(key, options, q_n, flagged=False):
    return {"q_n": q_n, "key": key, "options": options, "suggested_n": 1, "flagged": flagged,
            "item": {"owner": "shaw", "text": f"Do {key}", "counterpart": "Dana Reyes", "due_ts": None,
                     "due_source": "none stated", "quote": "q", "citation": "pocket:r1@00:10",
                     "call_title": "Synthetic Vendor Sync"}}


def _filed(led, *frozen):
    led.plan_card(Effect(CARD, "card", {"title": "Synthetic Vendor Sync"}), call_id="pocket:r1", frozen=list(frozen))
    led.apply_answers(CARD, status="submitted", answers={1: {"option_n": 1, "final": True}})
    led.run_pending(effects.Recorder())


def _reply(subject, body, *, to="postcall-dev", frm="pm-example", mid=None):
    with sqlite3.connect(os.environ["MSG_DB_PATH"]) as c:
        n = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        c.execute("INSERT INTO messages(id, type, from_agent, to_agent, subject, body, created_at) "
                  "VALUES (?,?,?,?,?,?,?)", (mid or f"msg_{n:04d}", "reply", frm, to, subject, body,
                                             f"2026-11-25 12:{n:02d}:00"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "pocket").mkdir()
    monkeypatch.setenv("POCKET_ROOT", str(tmp_path / "pocket"))
    _scratch_bus(os.environ["MSG_DB_PATH"])
    return ledger.Ledger(effects.ledger_path("test"))


def _no_calls(call):
    raise AssertionError("no calls in this test")


def _results(led):
    with sqlite3.connect(led.path) as c:
        return dict(c.execute("SELECT cmt, text FROM results").fetchall())


def test_sweep_records_a_tagged_seat_reply_once_and_plans_the_notice(env):
    led = env
    _filed(led, _frozen("k1", ASSIGN, 2))
    cmt = led.commitment_of("k1")
    _reply(f"Re: [postcall dispatch:{cmt}] draft the pricing deck", "Deck drafted: docs/deck-v1.md")
    _reply(f"Re: [postcall dispatch:{cmt}] draft the pricing deck", "to someone else", to="gm")
    _reply("untagged chatter", "not a result")

    world = effects.Recorder()
    out = sweep.run_once(now=NOW, extractor=_no_calls, adapter=world)
    assert out["results_recorded"] == 1
    assert _results(led) == {cmt: "Deck drafted: docs/deck-v1.md"}
    assert led.item_state("k1") == "done"
    assert out["results_planned"] == 1
    (notice,) = [e for e in world.sent if e.kind == "results"]
    assert "1/1 done" in notice.payload["body"]

    _reply(f"Re: [postcall dispatch:{cmt}] again", "a second reply")
    again = sweep.run_once(now=NOW + 60, extractor=_no_calls, adapter=world)
    assert again["results_recorded"] == 0
    assert _results(led) == {cmt: "Deck drafted: docs/deck-v1.md"}


def test_only_replies_for_our_dispatches_count(env):
    led = env
    _filed(led, _frozen("k1", ASSIGN, 2))
    _reply("Re: [postcall dispatch:cmt_000000000000] other", "someone else's work")
    assert pipeline.collect_replies(led, os.environ["MSG_DB_PATH"]) == 0
    assert _results(led) == {}


def test_notice_body_counts_unreviewed_and_due_pending_patch(tmp_path, monkeypatch):
    monkeypatch.delenv("POSTCALL_RESCHEDULE_PATCH", raising=False)
    led = ledger.Ledger(str(tmp_path / "l.db"))
    _filed(led, _frozen("k1", ASSIGN, 2), _frozen("k2", TAKE, 3), _frozen("k3", TAKE, 4, flagged=True))
    led.record_due_change(led.commitment_of("k2"), "2026-11-30T17:00:00-05:00", "by Monday", "pocket:r2")
    led.record_result(led.commitment_of("k1"), "deck drafted")
    soon = dt.datetime.now(dt.timezone.utc)
    assert pipeline.results_due(led, soon) == [f"results:{CARD}"]
    (n,) = [e for e in led.effects() if e["kind"] == "results"]
    body = json.loads(n["payload_json"])["body"]
    assert "1/1 done" in body and "unreviewed 1" in body and "due_pending_patch 1" in body


def test_card_with_no_dispatch_but_unreviewed_items_gets_one_notice(tmp_path):
    led = ledger.Ledger(str(tmp_path / "l.db"))
    _filed(led, _frozen("k1", TAKE, 2), _frozen("k2", TAKE, 3, flagged=True))
    soon = dt.datetime.now(dt.timezone.utc)
    assert pipeline.results_due(led, soon) == [f"results:{CARD}"]
    (n,) = [e for e in led.effects() if e["kind"] == "results"]
    assert "unreviewed 1" in json.loads(n["payload_json"])["body"]
    assert pipeline.results_due(led, soon) == []
