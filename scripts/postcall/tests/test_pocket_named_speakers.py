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


def test_named_label_is_a_verified_speaker_without_any_calendar(base):
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


def test_participant_item_from_named_label_is_verified_with_counterpart(base):
    call = dataclasses.replace(base, transcript=NAMED)
    roster = participants.resolve(call, _raw(), [], shaw_emails=SHAW)
    dana, gen = participants.apply_trust([_item("participant", "Dana Reyes"),
                                          _item("participant", "SPEAKER_02")], roster)
    assert (dana.trust, dana.fields["counterpart"]) == ("verified", "Dana Reyes")
    assert gen.trust == "inferred"


def test_shaw_item_verified_without_calendar_when_every_other_voice_is_named(base):
    call = dataclasses.replace(base, transcript=NAMED_TWO)
    roster = participants.resolve(call, _raw(), [], shaw_emails=SHAW)
    [it] = participants.apply_trust([_item("shaw", "Shaw Cole")], roster)
    assert it.trust == "verified"


def test_shaw_item_stays_inferred_when_a_generic_voice_is_unidentified(base):
    call = dataclasses.replace(base, transcript=NAMED)
    roster = participants.resolve(call, _raw(), [], shaw_emails=SHAW)
    [it] = participants.apply_trust([_item("shaw", "Shaw Cole")], roster)
    assert it.trust == "inferred"


def test_calendar_adds_the_email_of_a_named_invitee(base):
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
