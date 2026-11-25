"""Applying answers (design §4 resolution, §5 first-applied-wins, §6 dispatch authorization)."""
import pytest

from postcall import effects, ledger
from postcall.effects import Effect

TAKE = [{"action": "take"}, {"action": "drop"}]
ASSIGN = [{"action": "assign", "seat": "pm-example", "agent_work": "draft the deck"}, {"action": "take"},
          {"action": "drop"}]


def _frozen(key, options, q_n=2, flagged=False):
    return {"q_n": q_n, "key": key, "options": options, "suggested_n": 1, "flagged": flagged}


@pytest.fixture
def led(tmp_path):
    return ledger.Ledger(str(tmp_path / "ledger.db"))


def _card(led, card_id, *frozen):
    led.plan_card(Effect(card_id, "card", {"frozen": list(frozen)}), call_id="pocket:r1", frozen=list(frozen))


def _ans(q1, **items):
    out = {1: {"option_n": q1, "final": True}}
    for q, (opt, final) in items.items():
        out[int(q[1:])] = {"option_n": opt, "final": final}
    return out


def _effects(led, kind=None):
    return [e for e in led.effects() if e["kind"] != "card" and (kind is None or e["kind"] == kind)]


def test_answers_on_an_unsubmitted_questionnaire_do_nothing(led):
    _card(led, "card:a", _frozen("k1", TAKE))
    assert led.apply_answers("card:a", status="pending", answers=_ans(1)) == "not_submitted"
    assert _effects(led) == []


def test_accept_all_applies_suggestions_but_not_to_flagged_items(led):
    _card(led, "card:a", _frozen("k1", TAKE), _frozen("k2", TAKE, q_n=3, flagged=True))
    led.apply_answers("card:a", status="submitted", answers=_ans(1))
    assert [e["kind"] for e in _effects(led)] == ["task"]
    assert led.decision("k1") == "take"
    assert led.decision("k2") is None and led.item_state("k2") == "unreviewed"


def test_explicit_answer_wins_over_drop_all(led):
    _card(led, "card:a", _frozen("k1", TAKE), _frozen("k2", TAKE, q_n=3))
    led.apply_answers("card:a", status="submitted", answers=_ans(3, q2=(1, True)))
    assert led.decision("k1") == "take"
    assert led.decision("k2") == "drop"


def test_decide_per_item_leaves_unanswered_unreviewed(led):
    _card(led, "card:a", _frozen("k1", TAKE))
    led.apply_answers("card:a", status="submitted", answers=_ans(2))
    assert led.decision("k1") is None and led.item_state("k1") == "unreviewed"
    assert _effects(led) == []


def test_draft_item_answer_is_ignored(led):
    _card(led, "card:a", _frozen("k1", TAKE))
    led.apply_answers("card:a", status="submitted", answers=_ans(2, q2=(1, False)))
    assert led.decision("k1") is None


def test_assign_plans_one_dispatch_with_the_frozen_seat_and_scope(led):
    _card(led, "card:a", _frozen("k1", ASSIGN))
    led.apply_answers("card:a", status="submitted", answers=_ans(2, q2=(1, True)))
    d, = _effects(led, "dispatch")
    cmt = led.commitment_of("k1")
    assert d["effect_id"] == f"dispatch:{cmt}"
    import json
    p = json.loads(d["payload_json"])
    assert (p["seat"], p["agent_work"], p["decision_src"]) == ("pm-example", "draft the deck", "card:a#2")


def test_first_applied_answer_wins_across_cards(led):
    _card(led, "card:a", _frozen("k1", TAKE))
    _card(led, "card:b", _frozen("k1", ASSIGN))
    led.apply_answers("card:b", status="submitted", answers=_ans(2, q2=(1, True)))
    led.apply_answers("card:a", status="submitted", answers=_ans(2, q2=(1, True)))
    assert led.decision("k1") == "assign"
    assert [e["kind"] for e in _effects(led)] == ["dispatch"]


def test_applying_the_same_card_twice_adds_nothing(led):
    _card(led, "card:a", _frozen("k1", ASSIGN))
    led.apply_answers("card:a", status="submitted", answers=_ans(1))
    led.apply_answers("card:a", status="submitted", answers=_ans(1))
    assert len(_effects(led)) == 1


def test_dispatch_whose_decision_source_does_not_match_is_refused_not_sent(led):
    _card(led, "card:a", _frozen("k1", ASSIGN))
    led.apply_answers("card:a", status="submitted", answers=_ans(1))
    cmt = led.commitment_of("k1")
    forged = Effect(f"dispatch:{cmt}x", "dispatch", {"seat": "pm-other", "agent_work": "x", "decision_src": "card:z#9",
                                                     "cmt": cmt})
    led.plan(forged, call_id="pocket:r1")
    world = effects.Recorder()
    led.run_pending(world)
    assert [e.kind for e in world.sent] == ["card", "dispatch"]
    assert {e["effect_id"]: e["state"] for e in led.effects()}[forged.effect_id] == "refused"
