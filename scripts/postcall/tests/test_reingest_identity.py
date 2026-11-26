"""Re-extraction of the same call reconciles against its earlier occurrences before minting anything
(design §5 "Re-extraction of the same call", review round 2 B2). The occurrence key includes the
LLM's deliverable wording, so a reworded re-extraction must never land as a silent duplicate."""
import json

import pytest

from postcall import effects, extract, ledger, pipeline

from . import _calls

DECK = "I'll send you the pricing deck by Friday."
CONTRACT = "I will also send you the signed contract by Monday."


@pytest.fixture(autouse=True)
def _verified(monkeypatch):
    _calls.verified(monkeypatch)


@pytest.fixture
def led(tmp_path):
    return ledger.Ledger(str(tmp_path / "ledger.db"))


def _first(led, world):
    call1, ex1 = _calls.call([_calls.item(DECK)], content_hash="h1")
    assert pipeline.process(call1, ledger=led, extractor=ex1, effects=world).status == "carded"
    card = [e for e in world.sent if e.kind == "card" and e.effect_id.startswith("card:pocket:c1:")][0]
    return card.payload["frozen"][0]["key"], led.commitment_of(card.payload["frozen"][0]["key"])


def _again(items, *, seen=None):
    call2, _ = _calls.call(items, content_hash="h2")
    raw = {"shaw_label": "SPEAKER_00", "shaw_label_evidence": "This is Shaw.", "items": list(items)}

    def ex(_c, existing=None):
        if seen is not None:
            seen.append(existing)
        return extract.Extraction(ok=True, raw=raw)
    return call2, ex


def _new_card(world):
    cards = [e for e in world.sent if e.kind == "card" and e.effect_id.startswith("card:pocket:c1:")]
    return cards[1] if len(cards) > 1 else None


def test_the_reextraction_is_given_the_calls_existing_occurrences(led):
    world = effects.Recorder()
    old_key, _ = _first(led, world)
    seen = []
    call2, ex2 = _again([_calls.item(DECK)], seen=seen)
    pipeline.process(call2, ledger=led, extractor=ex2, effects=world)
    assert len(seen) == 1 and [e["occ"] for e in seen[0]] == [old_key]
    assert seen[0][0]["quote"] == DECK and seen[0][0]["owner"] == "shaw"


def test_a_reworded_deliverable_on_the_same_quote_is_uncertain_not_a_silent_duplicate(led):
    world = effects.Recorder()
    old_key, old_cmt = _first(led, world)
    call2, ex2 = _again([_calls.item(DECK, deliverable="deck with pricing")])
    assert pipeline.process(call2, ledger=led, extractor=ex2, effects=world).status == "carded"
    new = _new_card(world)
    (f,) = new.payload["frozen"]
    assert f["flagged"] is True
    assert {"action": "same_as", "cmt": old_cmt} in f["options"]
    assert new.payload["questions"][1]["prompt"].startswith("⚠ ")
    # accept-all never acts on it
    led.apply_answers(new.effect_id, status="submitted", answers={1: {"option_n": 1, "final": True}})
    assert led.decision(f["key"]) is None
    assert [e for e in led.effects() if e["kind"] == "task" and json.loads(e["payload_json"])["key"] == f["key"]] == []


def test_an_accepted_same_as_occ_with_equal_fields_files_no_card(led):
    world = effects.Recorder()
    old_key, old_cmt = _first(led, world)
    longer = DECK + " You have my word on it."
    call2, ex2 = _again([_calls.item(longer, same_as_occ=old_key)])
    assert pipeline.process(call2, ledger=led, extractor=ex2, effects=world).status == "no_new_items"
    assert _new_card(world) is None
    new_key = pipeline._source_key(call2, longer, _calls.item(longer))
    assert led.commitment_of(new_key) == old_cmt                       # recorded, so it is never re-asked


@pytest.mark.parametrize("change", [{"operation": "draft"}, {"deliverable": "price list"},
                                    {"owner": "participant", "text": "Dana sends the pricing deck"}])
def test_a_same_as_occ_claim_that_fails_a_field_check_is_uncertain(led, change):
    world = effects.Recorder()
    old_key, old_cmt = _first(led, world)
    call2, ex2 = _again([_calls.item(DECK, same_as_occ=old_key, **change)])
    assert pipeline.process(call2, ledger=led, extractor=ex2, effects=world).status == "carded"
    (f,) = _new_card(world).payload["frozen"]
    assert f["flagged"] is True and {"action": "same_as", "cmt": old_cmt} in f["options"]


def test_two_items_claiming_one_old_occurrence_are_never_merged(led):
    """One restates the old occurrence exactly, the other claims it too: the claim is not one-to-one,
    so the second is asked with ⚠ even though its fields and span pass."""
    world = effects.Recorder()
    old_key, old_cmt = _first(led, world)
    longer = DECK + " You have my word on it."
    items = [_calls.item(DECK, same_as_occ=old_key), _calls.item(longer, same_as_occ=old_key)]
    call2, _ = _calls.call([items[1]], content_hash="h2")           # one turn holds both quotes
    raw = {"shaw_label": "SPEAKER_00", "shaw_label_evidence": "This is Shaw.", "items": items}
    ex2 = lambda _c, existing=None: extract.Extraction(ok=True, raw=raw)
    assert pipeline.process(call2, ledger=led, extractor=ex2, effects=world).status == "carded"
    (f,) = _new_card(world).payload["frozen"]
    assert f["item"]["quote"] == longer
    assert f["flagged"] and {"action": "same_as", "cmt": old_cmt} in f["options"]


def test_a_cross_call_auto_link_never_overrides_an_uncertain_restatement(led):
    """The reworded item matches another call's commitment on every auto-link rule, but it may also
    restate this call's own earlier occurrence: Shaw decides, nothing links on its own."""
    world = effects.Recorder()
    other, ex0 = _calls.call([_calls.item("Our deck with pricing goes out to you this week.",
                                          deliverable="deck with pricing")], call_id="pocket:c0")
    assert pipeline.process(other, ledger=led, extractor=ex0, effects=world).status == "carded"
    old_key, old_cmt = _first(led, world)
    call2, ex2 = _again([_calls.item(DECK, deliverable="deck with pricing")])
    assert pipeline.process(call2, ledger=led, extractor=ex2, effects=world).status == "carded"
    cards = [e for e in world.sent if e.kind == "card"]
    (f,) = cards[2].payload["frozen"]
    assert f["flagged"] and {"action": "same_as", "cmt": old_cmt} in f["options"]


def test_a_genuinely_new_item_elsewhere_in_the_call_is_not_flagged(led):
    world = effects.Recorder()
    _first(led, world)
    call2, ex2 = _again([_calls.item(DECK), _calls.item(CONTRACT, deliverable="signed contract",
                                                        timeframe="by Monday")])
    assert pipeline.process(call2, ledger=led, extractor=ex2, effects=world).status == "carded"
    (f,) = _new_card(world).payload["frozen"]
    assert f["item"]["quote"] == CONTRACT and f["flagged"] is False


# --- review round 3: shoulds and test gaps ---------------------------------------------------------

def test_an_equal_fields_claim_on_a_quote_elsewhere_in_the_call_is_uncertain(led):
    """T1: the claim's fields pass but its quote neither overlaps nor equals the old one."""
    world = effects.Recorder()
    old_key, old_cmt = _first(led, world)
    elsewhere = "Like I said, the pricing deck will be with you before the weekend."
    claim = _calls.item(elsewhere, same_as_occ=old_key)
    call2, _ = _calls.call([_calls.item(DECK), claim], content_hash="h2")   # the old quote is still there
    raw = {"shaw_label": "SPEAKER_00", "shaw_label_evidence": "This is Shaw.", "items": [claim]}
    ex2 = lambda _c, existing=None: extract.Extraction(ok=True, raw=raw)    # the only claimant
    assert pipeline.process(call2, ledger=led, extractor=ex2, effects=world).status == "carded"
    (f,) = _new_card(world).payload["frozen"]
    assert f["item"]["quote"] == elsewhere
    assert f["flagged"] and {"action": "same_as", "cmt": old_cmt} in f["options"]


def test_a_claim_with_a_different_action_type_is_uncertain(led):
    """T2."""
    world = effects.Recorder()
    old_key, old_cmt = _first(led, world)
    call2, ex2 = _again([_calls.item(DECK, action_type="email", same_as_occ=old_key)])
    assert pipeline.process(call2, ledger=led, extractor=ex2, effects=world).status == "carded"
    (f,) = _new_card(world).payload["frozen"]
    assert f["flagged"] and {"action": "same_as", "cmt": old_cmt} in f["options"]


def test_two_new_items_both_claiming_one_old_occurrence_are_both_uncertain(led):
    """T3: neither is a known key; both pass the field and span checks; neither wins."""
    world = effects.Recorder()
    old_key, old_cmt = _first(led, world)
    a = DECK + " You have my word on it."
    b = "Honestly, " + DECK
    turn = "Honestly, " + DECK + " You have my word on it."
    items = [_calls.item(a, same_as_occ=old_key), _calls.item(b, same_as_occ=old_key)]
    call2, _ = _calls.call([_calls.item(turn)], content_hash="h2")
    raw = {"shaw_label": "SPEAKER_00", "shaw_label_evidence": "This is Shaw.", "items": items}
    ex2 = lambda _c, existing=None: extract.Extraction(ok=True, raw=raw)
    assert pipeline.process(call2, ledger=led, extractor=ex2, effects=world).status == "carded"
    frozen = _new_card(world).payload["frozen"]
    assert sorted(f["item"]["quote"] for f in frozen) == sorted([a, b])
    assert all(f["flagged"] and {"action": "same_as", "cmt": old_cmt} in f["options"] for f in frozen)


def test_an_overlap_with_a_different_owner_and_no_claim_is_a_new_item(led):
    """T4: the overlap rule is same-owner only (a non-diarized call keeps both owners in one text)."""
    world = effects.Recorder()
    old_key, old_cmt = _first(led, world)
    theirs = _calls.item(DECK, owner="participant", text="Dana gets the pricing deck from Shaw")
    call2, ex2 = _again([theirs])
    assert pipeline.process(call2, ledger=led, extractor=ex2, effects=world).status == "carded"
    (f,) = _new_card(world).payload["frozen"]
    assert {"action": "same_as", "cmt": old_cmt} not in f["options"]


def test_the_same_as_target_is_the_overlapping_occurrence_whose_fields_match(led):
    """S1: one sentence, two deliverables. The deck is restated exactly (known); the reworded contract
    item must be offered `Same as` the contract, never the deck."""
    world = effects.Recorder()
    both = "I'll send you the pricing deck and the signed contract by Friday."
    call1, ex1 = _calls.call([_calls.item(both), _calls.item(both, deliverable="signed contract")],
                             content_hash="h1")
    assert pipeline.process(call1, ledger=led, extractor=ex1, effects=world).status == "carded"
    keys = {f["item"]["text"]: f["key"] for f in world.sent[0].payload["frozen"]}
    contract_cmt = led.commitment_of(keys["Send Dana the signed contract"])
    call2, ex2 = _again([_calls.item(both), _calls.item(both, deliverable="contract, signed")])
    assert pipeline.process(call2, ledger=led, extractor=ex2, effects=world).status == "carded"
    (f,) = _new_card(world).payload["frozen"]
    same = [o for o in f["options"] if o["action"] == "same_as"]
    assert same == [{"action": "same_as", "cmt": contract_cmt}]


def test_an_accepted_claim_records_a_moved_due_date(led):
    """S2: a re-transcription that corrects Friday to Monday is a due change, never lost."""
    world = effects.Recorder()
    old_key, old_cmt = _first(led, world)
    monday = DECK + " Actually, make that Monday."
    call2, ex2 = _again([_calls.item(monday, timeframe="by Monday", same_as_occ=old_key)])
    assert pipeline.process(call2, ledger=led, extractor=ex2, effects=world).status == "no_new_items"
    changes = led.due_changes(old_cmt)
    assert len(changes) == 1 and changes[0]["due_source"] == "by Monday"


@pytest.mark.parametrize("bad", [["x"], {"occ": "x"}, 7])
def test_a_same_as_occ_that_is_not_a_string_is_ignored(led, bad):
    """S5: never a TypeError (which would burn the call's attempts)."""
    world = effects.Recorder()
    old_key, old_cmt = _first(led, world)
    call2, ex2 = _again([_calls.item(DECK, deliverable="deck with pricing", same_as_occ=bad)])
    assert pipeline.process(call2, ledger=led, extractor=ex2, effects=world).status == "carded"
    (f,) = _new_card(world).payload["frozen"]
    assert f["flagged"] and {"action": "same_as", "cmt": old_cmt} in f["options"]


def test_failed_attempts_count_per_transcript(led):
    """S3: a new transcript starts with a fresh attempt budget."""
    for _ in range(ledger.MAX_CALL_ATTEMPTS - 1):
        led.set_call_status("pocket:c1", "failed_retry", content_hash="h1", error="x")
    led.set_call_status("pocket:c1", "failed_retry", content_hash="h2", error="x")
    row = led.call_row("pocket:c1")
    assert row["status"] == "failed_retry" and row["attempts"] == 1
    for _ in range(ledger.MAX_CALL_ATTEMPTS - 1):
        led.set_call_status("pocket:c1", "failed_retry", content_hash="h2", error="x")
    assert led.call_row("pocket:c1")["status"] == "failed_final"
