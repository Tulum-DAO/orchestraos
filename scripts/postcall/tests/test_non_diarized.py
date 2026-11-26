"""A call with no diarization (design §2 speaker binding): grounded items are kept with speaker
"unknown" (speaker "" and trust "unknown"), the owner taken from the extractor, and no Shaw-label
evidence is required. Every such item is ⚠ and needs Shaw's own answer; accept-all skips it."""
import json
import os

import pytest

from postcall import effects, extract, ledger, pipeline, sources


@pytest.fixture
def call(fixture_dir):
    return sources.load_pocket(os.path.join(fixture_dir, "pocket", "non-diarized-call"))


@pytest.fixture
def extraction(fixture_dir):
    with open(os.path.join(fixture_dir, "llm", "non-diarized-ok.json")) as f:
        return extract.Extraction(ok=True, raw=json.load(f))


def test_non_diarized_items_are_kept_with_unknown_speaker(call, extraction):
    assert call.diarized is False and extract.turns(call.transcript) == []
    items = extract.ground(call, extraction)
    assert [(i.owner, i.speaker, i.trust) for i in items] == [("shaw", "", "unknown"), ("participant", "", "unknown")]
    assert items[0].citation == f"{call.call_id}@00:00"


def test_non_diarized_call_is_carded_all_flagged_and_accept_all_creates_nothing(tmp_path, call, extraction):
    led, world = ledger.Ledger(str(tmp_path / "l.db")), effects.Recorder()
    result = pipeline.process(call, ledger=led, extractor=lambda c: extraction, effects=world)
    assert result.status == "carded"
    (card,) = [e for e in world.sent if e.kind == "card"]
    assert len(card.payload["frozen"]) == 2
    assert all(q["prompt"].startswith("⚠ ") for q in card.payload["questions"][1:])
    assert all(f["flagged"] for f in card.payload["frozen"])
    led.apply_answers(card.effect_id, status="submitted", answers={1: {"option_n": 1, "final": True}})
    assert [e for e in led.effects() if e["kind"] != "card"] == []
    assert {led.item_state(f["key"]) for f in card.payload["frozen"]} == {"unreviewed"}


def test_diarized_call_without_turns_is_treated_as_non_diarized(call, extraction):
    import dataclasses
    items = extract.ground(dataclasses.replace(call, diarized=True), extraction)
    assert [i.trust for i in items] == ["unknown", "unknown"]


def test_non_diarized_owner_must_still_be_shaw_or_participant(call, extraction):
    raw = dict(extraction.raw, items=[dict(extraction.raw["items"][0], owner="someone")])
    assert extract.ground(call, extract.Extraction(ok=True, raw=raw)) == []
