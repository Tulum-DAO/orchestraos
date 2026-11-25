"""An empty or below-floor call never yields a card (design §2; the 2026-11-19 lesson), and an
extractor failure is not mistaken for "no commitments" (codex round 2, finding 7)."""
import json
import os

import pytest

from postcall import effects, extract, ledger, pipeline, sources


def _pocket(fixture_dir, name):
    return sources.load_pocket(os.path.join(fixture_dir, "pocket", name))


def _canned(fixture_dir, name):
    with open(os.path.join(fixture_dir, "llm", name)) as f:
        payload = json.load(f)
    calls = []

    def extractor(call):
        calls.append(call.call_id)
        return extract.Extraction(ok=True, raw=payload)

    extractor.calls = calls
    return extractor


def _never_called(call):
    raise AssertionError("extractor must not run for this call")


@pytest.fixture
def led(tmp_path):
    return ledger.Ledger(str(tmp_path / "ledger.db"))


@pytest.fixture
def fx():
    return effects.Recorder()


def test_short_call_never_reaches_the_extractor(fixture_dir, led, fx):
    call = _pocket(fixture_dir, "fragment-90s")
    result = pipeline.process(call, ledger=led, extractor=_never_called, effects=fx)
    assert result.status == "skipped_floor"
    assert fx.sent == []


def test_non_call_category_never_reaches_the_extractor(fixture_dir, led, fx):
    call = _pocket(fixture_dir, "ad-category")
    result = pipeline.process(call, ledger=led, extractor=_never_called, effects=fx)
    assert result.status == "skipped_floor"
    assert fx.sent == []


def test_extractor_with_no_items_files_no_card(fixture_dir, led, fx):
    call = _pocket(fixture_dir, "two-party-call")

    def empty(call):
        return extract.Extraction(ok=True, raw={"shaw_label": "SPEAKER_00", "items": []})

    result = pipeline.process(call, ledger=led, extractor=empty, effects=fx)
    assert result.status == "no_card"
    assert fx.sent == []
    assert led.call_status(call.call_id) == "no_card"


def test_extractor_failure_is_retried_not_marked_empty(fixture_dir, led, fx):
    call = _pocket(fixture_dir, "two-party-call")

    def broken(call):
        return extract.Extraction(ok=False, raw=None, error="timeout")

    result = pipeline.process(call, ledger=led, extractor=broken, effects=fx)
    assert result.status == "failed_retry"
    assert fx.sent == []
    assert led.call_status(call.call_id) == "failed_retry"


def test_fabricated_quotes_are_grounded_away_and_no_card_is_filed(fixture_dir, led, fx):
    call = _pocket(fixture_dir, "two-party-call")
    result = pipeline.process(call, ledger=led, extractor=_canned(fixture_dir, "two-party-fabricated.json"),
                              effects=fx)
    assert result.status == "no_card"
    assert fx.sent == []


def test_grounded_items_plan_exactly_one_card(fixture_dir, led, fx):
    call = _pocket(fixture_dir, "two-party-call")
    result = pipeline.process(call, ledger=led, extractor=_canned(fixture_dir, "two-party-ok.json"), effects=fx)
    assert result.status == "carded"
    cards = [e for e in fx.sent if e.kind == "card"]
    assert len(cards) == 1
    assert len(result.items) == 2
    assert cards[0].payload["summary"].startswith("Decide by: ")
    assert cards[0].payload["summary"].splitlines()[1].endswith("· With: Dana Reyes (unverified)")
    prompts = [q["prompt"] for q in cards[0].payload["questions"][1:]]
    assert prompts[0].startswith("⚠ Send Dana the pricing deck · Dana Reyes · Due: Fri 27 Nov 17:00 Tulum")
    assert prompts[1].startswith("⚠ Dana Reyes owes you: Dana shares the API credentials · Due: Wed 25 Nov 09:00 Tulum")
