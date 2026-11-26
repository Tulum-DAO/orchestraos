"""Dedupe across calls and reschedules (design §5): a later call's restatement of an existing
commitment links to it instead of asking again, only when every auto-link rule holds; anything that
merely looks the same goes on the card as uncertain, and due-date changes are immutable, ordered rows."""
import dataclasses
import json

import pytest

from postcall import effects, extract, ledger, pipeline, sources

CALL1, CALL2 = "2026-11-24T19:00:00Z", "2026-11-26T19:00:00Z"  # Tue, Thu


def _item(quote, *, owner="shaw", operation="send", deliverable="pricing deck", recurring=False,
          timeframe="by Friday", trust="verified", action_type="document_send", counterpart="Dana Reyes",
          text="Send Dana the pricing deck"):
    return {"owner": owner, "kind": "commitment", "counterpart": counterpart, "action_type": action_type,
            "operation": operation, "deliverable": deliverable, "commitment_text": text,
            "timeframe": timeframe, "quote": quote, "recurring": recurring, "_trust": trust}


def _call(n, when, items):
    turns = ["**SPEAKER_00** _[00:01]_\nHi Dana, thanks for making time. This is Shaw."]
    for i, it in enumerate(items, 1):
        label = "SPEAKER_00" if it["owner"] == "shaw" else "SPEAKER_01"
        turns.append(f"**{label}** _[0{i}:00]_\n{it['quote']}")
    transcript = f"# Sync {n}\n\n## Transcript\n\n" + "\n\n".join(turns) + "\n"
    call = sources.Call(call_id=f"pocket:c{n}", source="pocket", title=f"Sync {n}", created_at=when,
                        duration_s=600.0, category="call-external", diarized=True, ingested=True,
                        transcript=transcript, content_hash=f"h{n}")
    raw = {"shaw_label": "SPEAKER_00", "shaw_label_evidence": "This is Shaw.", "items": items}
    return call, (lambda c: extract.Extraction(ok=True, raw=raw))


@pytest.fixture(autouse=True)
def _trust_from_fixture(monkeypatch):
    """Stand-in for participants.apply_trust: the fixture says which items are speaker-verified."""
    real = extract.ground

    def ground(call, extraction):
        return [dataclasses.replace(i, trust=i.fields.get("_trust", "inferred")) for i in real(call, extraction)]

    monkeypatch.setattr(extract, "ground", ground)


@pytest.fixture
def led(tmp_path):
    return ledger.Ledger(str(tmp_path / "ledger.db"))


@pytest.fixture
def world():
    return effects.Recorder()


def _run(led, world, n, when, *items):
    call, ex = _call(n, when, list(items))
    return pipeline.process(call, ledger=led, extractor=ex, effects=world)


def _cards(world, call_n):
    return [e for e in world.sent if e.kind == "card" and e.effect_id.startswith(f"card:pocket:c{call_n}:")]


def _first_key(led, call_n):
    card = {e["effect_id"]: e for e in led.effects()}[f"card:pocket:c{call_n}:r0:p1"]
    return json.loads(card["payload_json"])["frozen"][0]["key"]


def _answer(led, call_n, q1, **items):
    answers = {1: {"option_n": q1, "final": True}}
    answers.update({int(q[1:]): {"option_n": opt, "final": True} for q, opt in items.items()})
    return led.apply_answers(f"card:pocket:c{call_n}:r0:p1", status="submitted", answers=answers)


def _of_kind(led, kind):
    return [e for e in led.effects() if e["kind"] == kind]


DECK1 = "I'll send you the pricing deck by Friday."
DECK2 = "Let me send you the pricing deck by Monday instead."


def test_verified_identical_item_auto_links_with_no_new_question(led, world):
    _run(led, world, 1, CALL1, _item(DECK1))
    cmt = led.commitment_of(_first_key(led, 1))
    r = _run(led, world, 2, CALL2, _item(DECK2, timeframe="by Monday"),
             _item("I will book the venue for the offsite.", action_type="calendar_invite", operation="schedule",
                   deliverable="offsite venue", text="Book the offsite venue", timeframe=""))
    assert r.status == "carded"
    card, = _cards(world, 2)
    assert len(card.payload["questions"]) == 2 and "offsite venue" in card.payload["questions"][1]["prompt"]
    change, = led.due_changes(cmt)
    assert (change["seq"], change["due_ts"], change["due_source"], change["from_call"]) == (
        1, "2026-11-30T17:00:00-05:00", "by Monday", "pocket:c2")
    assert r.dupes["due_moved"] == 1


def test_inferred_item_is_uncertain_and_shown_with_a_same_as_option(led, world):
    _run(led, world, 1, CALL1, _item(DECK1))
    cmt = led.commitment_of(_first_key(led, 1))
    _run(led, world, 2, CALL2, _item(DECK2, timeframe="by Monday", trust="inferred"))
    card, = _cards(world, 2)
    q = card.payload["questions"][1]
    assert q["prompt"].startswith("⚠ ")
    assert q["menu"]["options"][-1]["label"] == 'Same as "Send Dana the pricing deck" (2026-11-24): update it'
    assert card.payload["frozen"][0]["options"][-1] == {"action": "same_as", "cmt": cmt}
    assert led.due_changes(cmt) == []  # nothing merges before Shaw picks it


def test_an_inferred_earlier_item_never_auto_links_either(led, world):
    _run(led, world, 1, CALL1, _item(DECK1, trust="inferred"))
    cmt = led.commitment_of(_first_key(led, 1))
    _run(led, world, 2, CALL2, _item(DECK2, timeframe="by Monday"))
    card, = _cards(world, 2)
    assert card.payload["frozen"][0]["options"][-1] == {"action": "same_as", "cmt": cmt}
    assert led.due_changes(cmt) == []


def test_draft_and_send_of_the_same_deliverable_stay_separate(led, world):
    _run(led, world, 1, CALL1, _item(DECK1))
    cmt = led.commitment_of(_first_key(led, 1))
    r = _run(led, world, 2, CALL2, _item("I'll draft the pricing deck by Monday.", operation="draft",
                                         timeframe="by Monday", text="Draft the pricing deck"))
    assert r.status == "carded"
    card, = _cards(world, 2)
    assert len(card.payload["questions"]) == 2
    assert led.due_changes(cmt) == []


def test_recurring_items_never_auto_link(led, world):
    _run(led, world, 1, CALL1, _item(DECK1, recurring=True))
    cmt = led.commitment_of(_first_key(led, 1))
    r = _run(led, world, 2, CALL2, _item(DECK2, timeframe="by Monday", recurring=True))
    assert r.status == "carded"
    assert len(_cards(world, 2)) == 1
    assert led.due_changes(cmt) == []


def test_every_item_a_duplicate_means_no_card(led, world):
    _run(led, world, 1, CALL1, _item(DECK1))
    r = _run(led, world, 2, CALL2, _item(DECK2, timeframe="by Monday"))
    assert r.status == "no_card_dupes"
    assert led.call_status("pocket:c2") == "no_card_dupes"
    assert _cards(world, 2) == [] and [e for e in led.effects() if e["effect_id"].startswith("card:pocket:c2")] == []
    again = _run(led, world, 2, CALL2, _item(DECK2, timeframe="by Monday"))
    assert again.status == "already_processed"


def test_unchanged_due_records_no_change(led, world):
    _run(led, world, 1, CALL1, _item(DECK1))
    cmt = led.commitment_of(_first_key(led, 1))
    r = _run(led, world, 2, "2026-11-25T19:00:00Z", _item("Again: I'll send you the pricing deck by Friday."))
    assert r.status == "no_card_dupes"
    assert led.due_changes(cmt) == []


def test_assigned_commitment_gets_a_duenote_for_the_new_seq(led, world):
    _run(led, world, 1, CALL1, _item(DECK1))
    key = _first_key(led, 1)
    cmt = led.commitment_of(key)
    led._decide(key, {"action": "assign", "seat": "pm-example", "agent_work": "draft the deck"}, "card:x#2")
    _run(led, world, 2, CALL2, _item(DECK2, timeframe="by Monday"))
    note, = _of_kind(led, "duenote")
    assert note["effect_id"] == f"duenote:{cmt}:s1"
    p = json.loads(note["payload_json"])
    assert (p["to"], p["seq"], p["due_ts"], p["dispatch"]) == ("pm-example", 1, "2026-11-30T17:00:00-05:00",
                                                               f"dispatch:{cmt}")


def test_newer_due_change_obsoletes_an_older_planned_duenote(led, world):
    _run(led, world, 1, CALL1, _item(DECK1))
    key = _first_key(led, 1)
    cmt = led.commitment_of(key)
    led._decide(key, {"action": "assign", "seat": "pm-example", "agent_work": "draft the deck"}, "card:x#2")
    assert led.record_due_change(cmt, "2026-11-30T17:00:00-05:00", "by Monday", "pocket:c2") == 1
    assert led.record_due_change(cmt, "2026-12-01T17:00:00-05:00", "by Tuesday", "pocket:c3") == 2
    states = {e["effect_id"]: e["state"] for e in led.effects()}
    assert states[f"duenote:{cmt}:s1"] == "obsolete"
    assert states[f"duenote:{cmt}:s2"] == "planned"
    fresh = effects.Recorder()
    led.run_pending(fresh)
    assert [e.effect_id for e in fresh.sent if e.kind == "duenote"] == [f"duenote:{cmt}:s2"]
    assert [c["seq"] for c in led.due_changes(cmt)] == [1, 2]


def test_taken_commitment_records_pending_patch_while_the_flag_is_off(led, world, monkeypatch):
    monkeypatch.delenv("POSTCALL_RESCHEDULE_PATCH", raising=False)
    _run(led, world, 1, CALL1, _item(DECK1))
    key = _first_key(led, 1)
    cmt = led.commitment_of(key)
    led._decide(key, {"action": "take"}, "card:x#2")
    _run(led, world, 2, CALL2, _item(DECK2, timeframe="by Monday"))
    assert _of_kind(led, "patch") == []
    assert led.due_pending_patch(cmt) == 1


def test_taken_commitment_is_patched_when_the_flag_is_on(led, world, monkeypatch):
    monkeypatch.setenv("POSTCALL_RESCHEDULE_PATCH", "1")
    _run(led, world, 1, CALL1, _item(DECK1))
    key = _first_key(led, 1)
    cmt = led.commitment_of(key)
    led._decide(key, {"action": "take"}, "card:x#2")
    _run(led, world, 2, CALL2, _item(DECK2, timeframe="by Monday"))
    patch, = _of_kind(led, "patch")
    assert patch["effect_id"] == f"patch:{cmt}:s1"
    assert json.loads(patch["payload_json"])["due_ts"] == "2026-11-30T17:00:00-05:00"
    assert led.due_pending_patch(cmt) is None


def test_undecided_commitment_is_decided_with_the_latest_due(led, world):
    _run(led, world, 1, CALL1, _item(DECK1))
    _run(led, world, 2, CALL2, _item(DECK2, timeframe="by Monday"))
    _answer(led, 1, 1)
    task, = _of_kind(led, "task")
    item = json.loads(task["payload_json"])["item"]
    assert (item["due_ts"], item["due_source"]) == ("2026-11-30T17:00:00-05:00", "by Monday")


def test_chase_item_task_uses_the_chase_date_of_the_latest_due(led, world):
    promise = dict(owner="participant", action_type="document_send", deliverable="api credentials",
                   text="Dana shares the API credentials")
    _run(led, world, 1, CALL1, _item("I will share the API credentials with you tomorrow morning.",
                                     timeframe="tomorrow morning", **promise))
    _run(led, world, 2, CALL2, _item("I'll get you the API credentials by Monday.", timeframe="by Monday", **promise))
    _answer(led, 1, 1)
    task, = _of_kind(led, "task")
    assert json.loads(task["payload_json"])["chase_date"] == "2026-12-01T10:00:00-05:00"  # Mon + 1bd


def test_same_as_answer_links_the_item_and_records_its_due(led, world):
    _run(led, world, 1, CALL1, _item(DECK1))
    cmt = led.commitment_of(_first_key(led, 1))
    _run(led, world, 2, CALL2, _item(DECK2, timeframe="by Monday", trust="inferred"))
    key2 = _first_key(led, 2)
    card = {e["effect_id"]: e for e in led.effects()}["card:pocket:c2:r0:p1"]
    same_as_n = len(json.loads(card["payload_json"])["frozen"][0]["options"])
    _answer(led, 2, 2, q2=same_as_n)
    assert led.decision(key2) == "same_as"
    assert led.commitment_of(key2) == cmt
    change, = led.due_changes(cmt)
    assert (change["due_ts"], change["from_call"]) == ("2026-11-30T17:00:00-05:00", "pocket:c2")
    assert _of_kind(led, "task") == [] and _of_kind(led, "dispatch") == []


def test_done_within_14_days_is_not_asked_again(led, world):
    _run(led, world, 1, CALL1, _item(DECK1))
    cmt = led.commitment_of(_first_key(led, 1))
    led.record_result(cmt, "deck sent", now="2026-11-25T12:00:00Z")
    r = _run(led, world, 2, "2026-12-03T19:00:00Z", _item(DECK2, timeframe="by Monday"))
    assert r.status == "no_card_dupes" and r.dupes["already_closed"] == 1
    assert led.due_changes(cmt) == []


def test_done_14_days_or_more_ago_is_a_new_item(led, world):
    _run(led, world, 1, CALL1, _item(DECK1))
    cmt = led.commitment_of(_first_key(led, 1))
    led.record_result(cmt, "deck sent", now="2026-11-25T12:00:00Z")
    r = _run(led, world, 2, "2026-12-09T12:00:00Z", _item(DECK2, timeframe="by Monday"))
    assert r.status == "carded"
    card, = _cards(world, 2)
    assert not any("Same as" in o["label"] for o in card.payload["questions"][1]["menu"]["options"])
    assert led.commitment_of(_first_key(led, 2)) != cmt
