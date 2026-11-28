"""Speaker and owner binding (design §2): the quote must sit inside ONE speaker turn, that turn's
speaker decides attribution, and Pocket attribution is only ever 'inferred'."""
import os

import pytest

from postcall import extract, sources

QUOTE_SHAW = "I'll send you the pricing deck by Friday."
QUOTE_DANA = "I will share the API credentials with you tomorrow morning."


@pytest.fixture
def call(fixture_dir):
    return sources.load_pocket(os.path.join(fixture_dir, "pocket", "two-party-call"))


def _item(**kw):
    base = {"owner": "shaw", "kind": "commitment", "counterpart": "Dana Reyes", "action_type": "document_send",
            "operation": "send", "deliverable": "pricing deck", "commitment_text": "Send Dana the pricing deck",
            "timeframe": "by Friday", "quote": QUOTE_SHAW, "speaker": "SPEAKER_00"}
    base.update(kw)
    return base


def _run(call, *items, label="SPEAKER_00", evidence="This is Shaw."):
    raw = {"shaw_label": label, "shaw_label_evidence": evidence, "items": list(items)}
    return extract.ground(call, extract.Extraction(ok=True, raw=raw))


def test_shaw_item_in_shaw_turn_is_kept_as_inferred(call):
    items = _run(call, _item())
    assert len(items) == 1
    assert items[0].trust == "inferred"
    assert items[0].speaker == "SPEAKER_00"


def test_participant_item_in_other_turn_is_kept(call):
    items = _run(call, _item(owner="participant", quote=QUOTE_DANA, deliverable="api credentials",
                             commitment_text="Dana shares the API credentials", speaker="SPEAKER_01"))
    assert [i.owner for i in items] == ["participant"]


def test_owner_shaw_quoted_from_other_speaker_is_dropped(call):
    assert _run(call, _item(quote=QUOTE_DANA, speaker="SPEAKER_01")) == []


def test_owner_participant_quoted_from_shaw_is_dropped(call):
    assert _run(call, _item(owner="participant")) == []


def test_quote_spanning_two_turns_is_dropped(call):
    spanning = "talk soon. Let's go through the pilot."  # not contiguous in one turn
    assert _run(call, _item(quote="Shaw. Happy to, Shaw.")) == []
    assert _run(call, _item(quote=spanning)) == []


def test_unproven_shaw_label_fails_the_call(call):
    with pytest.raises(extract.AttributionError):
        _run(call, _item(), evidence="I am definitely Shaw, trust me.")


def test_shaw_label_evidence_must_come_from_that_label(call):
    with pytest.raises(extract.AttributionError):
        _run(call, _item(), evidence="Happy to, Shaw.")  # that line is SPEAKER_01's


def test_pleasantry_is_not_a_commitment(call):
    assert _run(call, _item(quote="Sounds good, talk soon.", commitment_text="talk soon")) == []


def test_pipeline_marks_unproven_label_as_failed_retry_with_no_card(call, tmp_path):
    from postcall import effects, ledger, pipeline
    led, fx = ledger.Ledger(str(tmp_path / "l.db")), effects.Recorder()

    def extractor(c):
        return extract.Extraction(ok=True, raw={"shaw_label": "SPEAKER_00", "shaw_label_evidence": "nope",
                                                "items": [_item()]})

    result = pipeline.process(call, ledger=led, extractor=extractor, effects=fx)
    assert result.status == "failed_retry"
    assert fx.sent == []


# --- a transcript whose speaker label IS Shaw's name needs no quoted proof (Glenn-call preview, 11-26)

def _named(label="Pat Example"):
    import dataclasses
    from postcall import sources
    t = ("# Sync\n\n## Transcript\n\n"
         f"**{label}** _[00:01]_\nThanks for joining.\n\n"
         "**Dana Reyes** _[00:05]_\nHappy to be here.\n\n"
         f"**{label}** _[00:09]_\nI'll send you the pricing deck by Friday.\n")
    return sources.Call(call_id="pocket:n1", source="pocket", title="Synthetic", created_at="2026-11-24T19:00:00Z",
                        duration_s=600.0, category="call-external", diarized=True, ingested=True, transcript=t,
                        content_hash="h")


def _named_raw(label="Pat Example", evidence=""):
    return extract.Extraction(ok=True, raw={"shaw_label": label, "shaw_label_evidence": evidence, "items": [
        {"owner": "shaw", "kind": "commitment", "quote": "I'll send you the pricing deck by Friday.",
         "commitment_text": "Send Dana the pricing deck", "counterpart": "Dana Reyes"}]})


def test_a_label_that_is_shaws_configured_name_is_its_own_proof(monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Pat Example")
    items = extract.ground(_named(), _named_raw())
    assert [i.owner for i in items] == ["shaw"]


def test_a_name_label_that_is_not_shaws_still_needs_proof(monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Someone Else")
    with pytest.raises(extract.AttributionError):
        extract.ground(_named(), _named_raw())


def test_without_configured_names_nothing_changes(monkeypatch):
    monkeypatch.delenv("POSTCALL_SHAW_NAMES", raising=False)
    with pytest.raises(extract.AttributionError):
        extract.ground(_named(), _named_raw())


def test_the_name_must_be_a_label_in_the_transcript(monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Pat Example")
    with pytest.raises(extract.AttributionError):
        extract.ground(_named(label="SPEAKER_00"), _named_raw(label="Pat Example"))


def test_two_labels_naming_shaw_are_both_his_voice(monkeypatch):
    """Shaw 2026-11-28 ("Shaw Cole is my name"): Pocket can split him into two labels that both carry
    a configured form of his name. Both are his voice; the claimed one proves itself (this replaces
    the "only one label naming Shaw" clause of gm msg_ac5f49bf)."""
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Pat Example,Pat")
    import dataclasses
    base = _named()
    call = dataclasses.replace(base, transcript=base.transcript +
                               "\n**Pat** _[00:20]_\nI will review the contract draft tonight.\n")
    raw = _named_raw()
    raw.raw["items"].append({"owner": "shaw", "kind": "commitment", "quote": "I will review the contract draft tonight.",
                             "commitment_text": "Review the contract draft", "counterpart": "Dana Reyes"})
    items = extract.ground(call, raw)
    assert [(i.owner, i.speaker) for i in items] == [("shaw", "Pat Example"), ("shaw", "Pat")]


def test_a_participant_item_from_a_second_shaw_label_is_dropped(monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Pat Example,Pat")
    import dataclasses
    base = _named()
    call = dataclasses.replace(base, transcript=base.transcript +
                               "\n**Pat** _[00:20]_\nI will review the contract draft tonight.\n")
    raw = _named_raw()
    raw.raw["items"] = [{"owner": "participant", "kind": "commitment", "quote": "I will review the contract draft tonight.",
                         "commitment_text": "Review the contract draft", "counterpart": "Pat"}]
    assert extract.ground(call, raw) == []


def test_split_shaw_claim_must_be_one_of_his_labels(monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Pat Example,Pat")
    import dataclasses
    base = _named()
    call = dataclasses.replace(base, transcript=base.transcript + "\n**Pat** _[00:20]_\nSure.\n")
    with pytest.raises(extract.AttributionError):
        extract.ground(call, _named_raw(label="Dana Reyes", evidence="Happy to be here."))



def test_a_name_label_proves_only_itself_not_another_claimed_label(monkeypatch):
    """POSTCALL_SHAW_NAMES names one label; the extractor claims the OTHER label: still needs proof."""
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Pat Example")
    with pytest.raises(extract.AttributionError):
        extract.ground(_named(), _named_raw(label="Dana Reyes"))
