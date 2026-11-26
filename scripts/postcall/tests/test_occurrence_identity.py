"""Two commitments inside one quote are two occurrences (design §5 occurrence identity): the source
key hashes the call, the normalized quote AND the normalized deliverable, action_type and operation,
so they get two commitment rows and each can be decided on its own."""
import dataclasses
import json
import sqlite3

import pytest

from postcall import effects, ledger, pipeline

from . import _calls

BOTH = "I'll send you the pricing deck and the case study by Friday."


@pytest.fixture(autouse=True)
def _verified(monkeypatch):
    _calls.verified(monkeypatch)


def test_one_quote_with_two_deliverables_is_two_commitments_a_task_and_a_dispatch(tmp_path, monkeypatch):
    real = pipeline._card_items

    def with_seat(call, items, location, **kw):   # the case study is agent-takeable by a resolved seat
        return [dataclasses.replace(ci, seat="pm-example", agent_work="draft the case study")
                if ci.text.endswith("case study") else ci for ci in real(call, items, location, **kw)]

    monkeypatch.setattr(pipeline, "_card_items", with_seat)
    led, world = ledger.Ledger(str(tmp_path / "ledger.db")), effects.Recorder()
    call, ex = _calls.call([_calls.item(BOTH, deliverable="pricing deck"),
                            _calls.item(BOTH, deliverable="case study")])
    assert pipeline.process(call, ledger=led, extractor=ex, effects=world).status == "carded"

    (card,) = [e for e in world.sent if e.kind == "card"]
    frozen = {f["item"]["text"]: f for f in card.payload["frozen"]}
    deck, study = frozen["Send Dana the pricing deck"], frozen["Send Dana the case study"]
    assert deck["key"] != study["key"]
    with sqlite3.connect(led.path) as c:
        assert c.execute("SELECT COUNT(*) FROM commitments").fetchone()[0] == 2

    assert study["options"][0]["action"] == "assign"
    led.apply_answers(card.effect_id, status="submitted", answers={
        1: {"option_n": 2, "final": True}, deck["q_n"]: {"option_n": 1, "final": True},
        study["q_n"]: {"option_n": 1, "final": True}})
    kinds = sorted(e["kind"] for e in led.effects() if e["kind"] != "card")
    assert kinds == ["dispatch", "task"]
    (task,) = [json.loads(e["payload_json"]) for e in led.effects() if e["kind"] == "task"]
    assert task["key"] == deck["key"]


def test_source_key_changes_with_deliverable_action_type_and_operation():
    call, _ = _calls.call([_calls.item(BOTH)])
    base = dict(deliverable="pricing deck", action_type="document_send", operation="send")
    keys = {pipeline._source_key(call, BOTH, base)}
    for k, v in (("deliverable", "case study"), ("action_type", "email"), ("operation", "draft")):
        keys.add(pipeline._source_key(call, BOTH, {**base, k: v}))
    assert len(keys) == 4
    # normalization: case and punctuation do not make a new occurrence
    assert pipeline._source_key(call, BOTH, {**base, "deliverable": "Pricing  Deck!"}) == \
        pipeline._source_key(call, BOTH, base)
