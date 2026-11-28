"""Shaw's choice on apr_e300aa64 (option 2): an unnamed voice does not hold Shaw's items when every
named voice is verified. Plus Grok's first-pass point on #663: a generic Shaw label is proven only
by a sentence that names him (a self-introduction), not by any sentence from that turn.

All data is synthetic: made-up names, example.com addresses."""
import dataclasses
import os

import pytest

from postcall import extract, extractor, participants, sources

SHAW = {"shaw@example.com"}

ONE_UNNAMED = """# Synthetic

## Transcript

**Shaw Cole** _[00:01]_
Thanks for joining, good to see you.

**Dana Reyes** _[00:06]_
Likewise. I will share the API credentials with you tomorrow morning.

**SPEAKER_02** _[00:10]_
Sounds good.

**Shaw Cole** _[03:00]_
I'll send you the pricing deck by Friday.
"""

ONLY_UNNAMED = ONE_UNNAMED.replace("**Dana Reyes**", "**SPEAKER_01**")


@pytest.fixture
def base(fixture_dir):
    return sources.load_pocket(os.path.join(fixture_dir, "pocket", "two-party-call"))


def _item(owner, speaker):
    return extract.Item(owner=owner, quote="q", span=(0, 1), citation="pocket:x@00:00", speaker=speaker,
                        trust="inferred", fields={"owner": owner, "counterpart": "someone", "quote": "q"})


def _shaw_trust(call, shaw_label="Shaw Cole", raw_extra=None, contacts=()):
    raw = {"shaw_label": shaw_label, "items": [], **(raw_extra or {})}
    roster = participants.resolve(call, raw, [], shaw_emails=SHAW, contacts=contacts)
    return participants.apply_trust([_item("shaw", shaw_label)], roster)[0].trust


# --- option 2: an unnamed voice is ignored when every named voice is verified ---------------------

def test_unnamed_voice_does_not_hold_shaw_items_when_named_voices_verify(base, monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole,Shaw")
    assert _shaw_trust(dataclasses.replace(base, transcript=ONE_UNNAMED)) == "verified"


def test_only_unnamed_voices_still_hold_shaw_items(base, monkeypatch):
    """No verified voice at all: nothing to stand on, so Shaw's items stay inferred."""
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole,Shaw")
    assert _shaw_trust(dataclasses.replace(base, transcript=ONLY_UNNAMED)) == "inferred"


def test_a_mapped_but_unverified_voice_still_holds_shaw_items(base, monkeypatch):
    """Option 2 ignores only UNNAMED voices; a voice mapped to an unverified name still counts."""
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole,Shaw")
    call = dataclasses.replace(base, transcript=ONE_UNNAMED.replace("Sounds good.", "Sam here, sounds good."))
    extra = {"speaker_names": [{"label": "SPEAKER_02", "name": "Sam Ortiz", "evidence": "Sam here, sounds good."}]}
    contacts = [{"name": "Sam Ortiz", "email": "sam@example.com"}]
    assert _shaw_trust(call, raw_extra=extra, contacts=contacts) == "inferred"


def test_unnamed_voice_rule_still_needs_shaw_names_configured(base):
    assert _shaw_trust(dataclasses.replace(base, transcript=ONE_UNNAMED)) == "inferred"


# --- Grok on #663: the proof sentence must name Shaw ---------------------------------------------

GENERIC = """# Synthetic

## Transcript

**SPEAKER_00** _[00:01]_
Hi, this is Shaw from the studio.

**SPEAKER_01** _[00:06]_
Great to meet you.

**SPEAKER_00** _[00:10]_
I'll send you the pricing deck by Friday.
"""


def _ground(call, label, evidence):
    item = {"owner": "shaw", "kind": "commitment", "quote": "I'll send you the pricing deck by Friday.",
            "commitment_text": "Send the pricing deck", "counterpart": "someone"}
    return extract.ground(call, extract.Extraction(ok=True, raw={"shaw_label": label,
                                                                  "shaw_label_evidence": evidence,
                                                                  "items": [item]}))


def test_generic_label_proven_by_a_sentence_naming_shaw(base, monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole,Shaw")
    items = _ground(dataclasses.replace(base, transcript=GENERIC), "SPEAKER_00", "Hi, this is Shaw from the studio.")
    assert [i.owner for i in items] == ["shaw"]


def test_generic_label_not_proven_by_a_sentence_without_his_name(base, monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole,Shaw")
    with pytest.raises(extract.AttributionError):
        _ground(dataclasses.replace(base, transcript=GENERIC), "SPEAKER_00",
                "I'll send you the pricing deck by Friday.")


def test_prompt_keeps_the_self_introduction_proof_and_drops_the_rate():
    prompt = extractor.build_prompt("x")[0]
    assert "a self-introduction" in prompt and "verbatim from Shaw's own turn" in prompt
    assert "1000" not in prompt


# --- Grok first pass on #713 ---------------------------------------------------------------------

GENERIC_THREE = GENERIC.replace("**SPEAKER_01** _[00:06]_\nGreat to meet you.",
                                "**Dana Reyes** _[00:06]_\nGreat to meet you.\n\n**SPEAKER_02** _[00:08]_\nHi.")


def test_unnamed_voice_still_holds_when_shaw_is_only_evidence_proven(base, monkeypatch):
    """The unnamed-voice relaxation needs Shaw identified by his configured name, not a quote."""
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole,Shaw")
    call = dataclasses.replace(base, transcript=GENERIC_THREE)
    roster = participants.resolve(call, {"shaw_label": "SPEAKER_00", "items": []}, [], shaw_emails=SHAW)
    assert roster.verified_names == {"Dana Reyes"}
    assert participants.apply_trust([_item("shaw", "SPEAKER_00")], roster)[0].trust == "inferred"


def test_surname_alone_does_not_prove_shaw(base, monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole,Shaw")
    call = dataclasses.replace(base, transcript=GENERIC.replace("Hi, this is Shaw from the studio.",
                                                                "Hi, Cole here from the studio."))
    with pytest.raises(extract.AttributionError):
        _ground(call, "SPEAKER_00", "Hi, Cole here from the studio.")


def test_name_word_inside_another_word_does_not_prove_shaw(base, monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole,Shaw")
    call = dataclasses.replace(base, transcript=GENERIC.replace("Hi, this is Shaw from the studio.",
                                                                "Hi, this is Shawna from the studio."))
    with pytest.raises(extract.AttributionError):
        _ground(call, "SPEAKER_00", "Hi, this is Shawna from the studio.")


def test_prompt_no_longer_offers_being_addressed_by_name_as_proof():
    assert "being addressed by name" not in extractor.build_prompt("x")[0]
