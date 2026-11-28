"""Pocket-named speakers (Shaw, 2026-11-27): a diarized Pocket recording whose speaker labels are
people's names identifies those people by itself. No calendar is needed to match speakers or to
verify them; the calendar only adds an email (for "by the next call") when a name is also invited.
Generic labels (SPEAKER_02) stay unverified unless the calendar evidence maps them.

All data is synthetic: made-up names, example.com addresses."""
import dataclasses
import os

import pytest

from postcall import calendar_ics as cal
from postcall import extract, participants, sources

from ._ics import Z

SHAW = {"shaw@example.com"}

NAMED = """# Synthetic Named Sync

## Transcript

**Shaw Cole** _[00:01]_
Thanks for joining, good to see you.

**Dana Reyes** _[00:06]_
Likewise. I will share the API credentials with you tomorrow morning.

**SPEAKER_02** _[00:10]_
I can send the integration checklist on Monday.

**Shaw Cole** _[03:00]_
I'll send you the pricing deck by Friday.
"""

NAMED_TWO = """# Synthetic Named Two-Party

## Transcript

**Shaw Cole** _[00:01]_
Thanks for joining, good to see you.

**Dana Reyes** _[00:06]_
Likewise. I will share the API credentials with you tomorrow morning.

**Shaw Cole** _[03:00]_
I'll send you the pricing deck by Friday.
"""


@pytest.fixture
def base(fixture_dir):
    return sources.load_pocket(os.path.join(fixture_dir, "pocket", "two-party-call"))


def _raw(shaw_label="Shaw Cole", names=()):
    raw = {"shaw_label": shaw_label, "shaw_label_evidence": "", "items": []}
    if names:
        raw["speaker_names"] = [{"label": l, "name": n, "evidence": e} for l, n, e in names]
    return raw


def _item(owner, speaker):
    return extract.Item(owner=owner, quote="q", span=(0, 1), citation="pocket:x@00:00", speaker=speaker,
                        trust="inferred", fields={"owner": owner, "counterpart": "someone", "quote": "q"})


def _calendar_with_dana():
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:sync-1@example.com\r\n"
           f"DTSTART:{Z(2026, 11, 24, 19, 0)}\r\nDTEND:{Z(2026, 11, 24, 19, 30)}\r\nSUMMARY:Sync\r\n"
           "ATTENDEE;CN=Shaw Cole;PARTSTAT=ACCEPTED:mailto:shaw@example.com\r\n"
           "ATTENDEE;CN=Dana Reyes;PARTSTAT=ACCEPTED:mailto:dana@example.com\r\n"
           "END:VEVENT\r\nEND:VCALENDAR\r\n")
    return cal.parse(ics)


def test_named_label_is_a_verified_speaker_without_any_calendar(base, monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole")
    call = dataclasses.replace(base, transcript=NAMED)
    roster = participants.resolve(call, _raw(), [], shaw_emails=SHAW)
    assert roster.event is None
    assert roster.label_names == {"Dana Reyes": "Dana Reyes"}
    assert roster.verified_names == {"Dana Reyes"}
    assert participants.with_line(roster) == ["Dana Reyes ✓"]


def test_generic_label_stays_unmapped_and_unverified_without_calendar(base):
    call = dataclasses.replace(base, transcript=NAMED)
    roster = participants.resolve(call, _raw(), [], shaw_emails=SHAW)
    assert "SPEAKER_02" not in roster.label_names


def test_participant_item_from_named_label_is_verified_with_counterpart(base, monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole")
    call = dataclasses.replace(base, transcript=NAMED)
    roster = participants.resolve(call, _raw(), [], shaw_emails=SHAW)
    dana, gen = participants.apply_trust([_item("participant", "Dana Reyes"),
                                          _item("participant", "SPEAKER_02")], roster)
    assert (dana.trust, dana.fields["counterpart"]) == ("verified", "Dana Reyes")
    assert gen.trust == "inferred"


def test_shaw_item_verified_without_calendar_when_every_other_voice_is_named(base, monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole")
    call = dataclasses.replace(base, transcript=NAMED_TWO)
    roster = participants.resolve(call, _raw(), [], shaw_emails=SHAW)
    [it] = participants.apply_trust([_item("shaw", "Shaw Cole")], roster)
    assert it.trust == "verified"


def test_shaw_item_stays_inferred_when_a_generic_voice_is_unidentified(base):
    call = dataclasses.replace(base, transcript=NAMED)
    roster = participants.resolve(call, _raw(), [], shaw_emails=SHAW)
    [it] = participants.apply_trust([_item("shaw", "Shaw Cole")], roster)
    assert it.trust == "inferred"


def test_calendar_adds_the_email_of_a_named_invitee(base, monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole")
    call = dataclasses.replace(base, transcript=NAMED_TWO)
    roster = participants.resolve(call, _raw(), _calendar_with_dana(), shaw_emails=SHAW)
    dana = [p for p in roster.participants if p.name == "Dana Reyes"]
    assert [(p.label, p.status, p.email) for p in dana] == [("Dana Reyes", "spoke", "dana@example.com")]
    assert roster.verified_names == {"Dana Reyes"}


def test_extractor_cannot_rename_a_pocket_named_label(base):
    call = dataclasses.replace(base, transcript=NAMED_TWO)
    raw = _raw(names=[("Dana Reyes", "Pat Quinn", "Likewise.")])
    roster = participants.resolve(call, raw, [], shaw_emails=SHAW)
    assert roster.label_names == {"Dana Reyes": "Dana Reyes"}


def test_shaws_own_named_label_is_never_a_participant(base):
    call = dataclasses.replace(base, transcript=NAMED_TWO)
    roster = participants.resolve(call, _raw(), [], shaw_emails=SHAW)
    assert "Shaw Cole" not in roster.label_names and "Shaw Cole" not in roster.verified_names


# --- review round 1 (fenced Claude reviewer; codex out of credits) ------------------------------

def _items_raw(shaw_label, evidence):
    item = {"owner": "shaw", "kind": "commitment", "counterpart": "Dana Reyes", "action_type": "document_send",
            "operation": "send", "deliverable": "pricing deck", "commitment_text": "Send Dana the pricing deck",
            "timeframe": "by Friday", "quote": "I'll send you the pricing deck by Friday.", "speaker": shaw_label}
    return {"shaw_label": shaw_label, "shaw_label_evidence": evidence, "items": [item]}


def test_ground_refuses_a_claimed_shaw_label_when_another_label_names_shaw(base, monkeypatch):
    """B1: Pocket labels Shaw by name, the extractor claims Dana's label: an attribution failure."""
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole")
    call = dataclasses.replace(base, transcript=NAMED_TWO)
    raw = _items_raw("Dana Reyes", "Likewise. I will share the API credentials with you tomorrow morning.")
    with pytest.raises(extract.AttributionError):
        extract.ground(call, extract.Extraction(ok=True, raw=raw))


def test_shaw_named_label_is_never_a_verified_participant_even_if_misclaimed(base, monkeypatch):
    """B1: even when shaw_label is wrong, the label naming Shaw never becomes a participant, and
    Shaw-side items do not verify."""
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole")
    call = dataclasses.replace(base, transcript=NAMED_TWO)
    roster = participants.resolve(call, _raw(shaw_label="Dana Reyes"), [], shaw_emails=SHAW)
    assert "Shaw Cole" not in roster.label_names and "Shaw Cole" not in roster.verified_names
    [it] = participants.apply_trust([_item("shaw", "Dana Reyes")], roster)
    assert it.trust == "inferred"


@pytest.mark.parametrize("label", [
    "SPEAKER_00", "SPEAKER_00a", "Speaker 3", "Speaker A", "Speaker B", "Unknown", "Unknown Speaker",
    "Guest Speaker", "Guest", "Person 1", "Voice 2", "S1", "Spkr 2", "Participant_A", "Host", "Me",
    "Other", "You", "Caller", "A", "", "Multiple Speakers", "Speakers", "Unknown Speakers", "Guests",
    "Participants", "SpeakerA", "Attendees",
])
def test_generic_labels_are_not_pocket_names(label):
    assert not participants._pocket_named(label)


@pytest.mark.parametrize("label", ["Dana Reyes", "Ivonne", "Mary-Jane O'Neil", "Dr. Lee", "Sanjay Bhatia",
                                   "Mary-Jane O\u2019Neil", "Jose\u0301 Garci\u0301a", "Ren\u00e9e"])
def test_person_names_are_pocket_names(label):
    assert participants._pocket_named(label)


SHORT_NAME = NAMED_TWO.replace("**Dana Reyes**", "**Dana**")


def test_pocket_name_is_kept_and_gets_no_email_from_a_partial_invitee_match(base):
    """S4: Pocket "Dana" stays "Dana"; only an exact invitee name lends an email."""
    call = dataclasses.replace(base, transcript=SHORT_NAME)
    roster = participants.resolve(call, _raw(), _calendar_with_dana(), shaw_emails=SHAW)
    spoke = [(p.name, p.email) for p in roster.participants if p.status == "spoke"]
    assert spoke == [("Dana", None)]


def test_next_call_due_resolves_through_a_named_invitee_label(base):
    """N7: "by the next call" works for a Pocket-named speaker whose name is an invitee."""
    future = cal.parse(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        "BEGIN:VEVENT\r\nUID:sync-1@example.com\r\n"
        f"DTSTART:{Z(2026, 11, 24, 19, 0)}\r\nDTEND:{Z(2026, 11, 24, 19, 30)}\r\nSUMMARY:Sync\r\n"
        "ATTENDEE;CN=Shaw Cole;PARTSTAT=ACCEPTED:mailto:shaw@example.com\r\n"
        "ATTENDEE;CN=Dana Reyes;PARTSTAT=ACCEPTED:mailto:dana@example.com\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:next-1@example.com\r\n"
        f"DTSTART:{Z(2026, 12, 1, 19, 0)}\r\nDTEND:{Z(2026, 12, 1, 20, 0)}\r\nSUMMARY:Check-in\r\n"
        "ATTENDEE;CN=Shaw Cole;PARTSTAT=ACCEPTED:mailto:shaw@example.com\r\n"
        "ATTENDEE;CN=Dana Reyes;PARTSTAT=ACCEPTED:mailto:dana@example.com\r\nEND:VEVENT\r\n"
        "END:VCALENDAR\r\n")
    call = dataclasses.replace(base, transcript=NAMED)
    roster = participants.resolve(call, _raw(), future, shaw_emails=SHAW)
    due = participants.next_call_due("Dana Reyes", roster, future, (), sources.call_end(call), "America/New_York")
    assert due is not None and due.due_ts.startswith("2026-12-01T13:00")


# --- review round 2 ------------------------------------------------------------------------------

def test_misclaim_without_configured_names_verifies_nobody(base):
    """R2 blocker: with POSTCALL_SHAW_NAMES unset, a person-named shaw_label cannot be proven, so a
    wrong claim (Dana as Shaw) must not make "Shaw Cole" a verified participant or verify Shaw items."""
    call = dataclasses.replace(base, transcript=NAMED_TWO)
    roster = participants.resolve(call, _raw(shaw_label="Dana Reyes"), [], shaw_emails=SHAW)
    assert roster.verified_names == set()
    shaw_it, part_it = participants.apply_trust([_item("shaw", "Dana Reyes"),
                                                 _item("participant", "Shaw Cole")], roster)
    assert shaw_it.trust == "inferred" and part_it.trust == "inferred"


def test_unconfigured_person_named_shaw_label_verifies_nobody(base):
    """Even a correct claim of a person-named label is unproven without the configured name."""
    call = dataclasses.replace(base, transcript=NAMED_TWO)
    roster = participants.resolve(call, _raw(), [], shaw_emails=SHAW)
    assert roster.verified_names == set()


def test_generic_shaw_label_with_evidence_still_verifies_pocket_names(base, monkeypatch):
    """A generic Shaw label (proven by quoted evidence in ground) verifies Pocket names when Shaw's
    names are configured (so a named label can be ruled out as his own voice)."""
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole")
    call = dataclasses.replace(base, transcript=NAMED_TWO.replace("**Shaw Cole**", "**SPEAKER_00**"))
    roster = participants.resolve(call, _raw(shaw_label="SPEAKER_00"), [], shaw_emails=SHAW)
    assert roster.verified_names == {"Dana Reyes"}


def test_shaw_name_comparison_is_whitespace_and_case_insensitive_everywhere(base, monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "shaw cole")
    call = dataclasses.replace(base, transcript=NAMED_TWO.replace("**Shaw Cole**", "**Shaw  Cole**"))
    raw = _items_raw("Dana Reyes", "Likewise. I will share the API credentials with you tomorrow morning.")
    with pytest.raises(extract.AttributionError):
        extract.ground(call, extract.Extraction(ok=True, raw=raw))


def test_shortcut_still_maps_a_generic_voice_when_shaw_is_name_labelled(base, monkeypatch):
    """Shortcut gate: Shaw-named label + SPEAKER_01 + one invitee maps SPEAKER_01 to the invitee."""
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole")
    call = dataclasses.replace(base, transcript=NAMED_TWO.replace("**Dana Reyes**", "**SPEAKER_01**"))
    roster = participants.resolve(call, _raw(), _calendar_with_dana(), shaw_emails=SHAW)
    assert roster.label_names == {"SPEAKER_01": "Dana Reyes"} and roster.verified_names == {"Dana Reyes"}


# --- review round 3 ------------------------------------------------------------------------------

def test_generic_misclaim_without_configured_names_verifies_nobody(base):
    """R3 blocker: variable unset, Pocket names Shaw, the extractor wrongly claims the generic voice.
    Nothing may verify: "Shaw Cole" is not a verified participant and Shaw items stay inferred."""
    call = dataclasses.replace(base, transcript=NAMED_TWO.replace("**Dana Reyes**", "**SPEAKER_01**"))
    roster = participants.resolve(call, _raw(shaw_label="SPEAKER_01"), [], shaw_emails=SHAW)
    assert roster.verified_names == set()
    shaw_it, part_it = participants.apply_trust([_item("shaw", "SPEAKER_01"),
                                                 _item("participant", "Shaw Cole")], roster)
    assert shaw_it.trust == "inferred" and part_it.trust == "inferred"


# --- Shaw split into several labels (Shaw 2026-11-28: "Shaw Cole is my name") ---------------------

SPLIT = """# Synthetic Split

## Transcript

**Shaw Cole** _[00:01]_
Thanks for joining, good to see you.

**Dana Reyes** _[00:06]_
Likewise. I will share the API credentials with you tomorrow morning.

**Cole** _[00:10]_
I'll send you the pricing deck by Friday.
"""


def test_split_shaw_labels_are_never_participants_and_others_verify(base, monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole,Shaw,Cole")
    call = dataclasses.replace(base, transcript=SPLIT)
    roster = participants.resolve(call, _raw(), [], shaw_emails=SHAW)
    assert roster.label_names == {"Dana Reyes": "Dana Reyes"} and roster.verified_names == {"Dana Reyes"}
    a, b, dana = participants.apply_trust([_item("shaw", "Shaw Cole"), _item("shaw", "Cole"),
                                           _item("participant", "Dana Reyes")], roster)
    assert (a.trust, b.trust, dana.trust) == ("verified", "verified", "verified")


def test_split_shaw_two_party_shortcut_counts_only_non_shaw_voices(base, monkeypatch):
    monkeypatch.setenv("POSTCALL_SHAW_NAMES", "Shaw Cole,Cole")
    call = dataclasses.replace(base, transcript=SPLIT.replace("**Dana Reyes**", "**SPEAKER_01**"))
    roster = participants.resolve(call, _raw(), _calendar_with_dana(), shaw_emails=SHAW)
    assert roster.label_names == {"SPEAKER_01": "Dana Reyes"} and roster.verified_names == {"Dana Reyes"}
