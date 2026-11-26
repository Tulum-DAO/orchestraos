"""Participants (design §2a, §3 rule 3): who was on a call, which diarization label is whom, how
much each mapping is trusted, and when the next call with a counterpart is.

All data is synthetic: made-up names, example.com addresses. The calendar is built inline as ICS
text and parsed with calendar_ics.parse, so each test shows exactly which events exist."""
import copy
import dataclasses
import os

import pytest

from postcall import calendar_ics as cal
from postcall import extract, participants, sources

from ._ics import Z

SHAW = {"shaw@example.com"}
TZ = "America/New_York"

# The fixture call runs 2026-11-24 19:00Z for 600 s. Every "call event" below overlaps it.
_CAL_HEAD = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
_CAL_TAIL = "END:VCALENDAR\r\n"


def _vevent(uid, start, end, summary, attendees, status=""):
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTART:{start}", f"DTEND:{end}", f"SUMMARY:{summary}"]
    if status:
        lines.append(f"STATUS:{status}")
    for cn, email, partstat in attendees:
        lines.append(f"ATTENDEE;CN={cn};PARTSTAT={partstat}:mailto:{email}")
    lines.append("END:VEVENT")
    return "\r\n".join(lines) + "\r\n"


SHAW_ATT = ("Shaw Cole", "shaw@example.com", "ACCEPTED")
DANA_ATT = ("Dana Reyes", "dana@example.com", "ACCEPTED")
PAT_ATT = ("Pat Quinn", "pat@example.com", "ACCEPTED")
LEE_DECLINED = ("Lee Park", "lee@example.com", "DECLINED")


def _calendar(*vevents):
    return cal.parse(_CAL_HEAD + "".join(vevents) + _CAL_TAIL)


def _call_event(*attendees):
    return _vevent("sync-1@example.com", Z(2026, 11, 24, 19, 0), Z(2026, 11, 24, 19, 30), "Vendor sync",
                   attendees)


# One invited non-Shaw attendee (Lee declined, so does not count): the two-party shortcut applies.
ONE_INVITEE = _calendar(_call_event(SHAW_ATT, DANA_ATT, LEE_DECLINED))
# Two invited non-Shaw attendees: the shortcut must not apply; only evidenced mappings count.
TWO_INVITEES = _calendar(_call_event(SHAW_ATT, DANA_ATT, PAT_ATT))

SAM_CONTACT = {"name": "Sam Ortiz", "aliases": ["Sammy"], "email": "sam@example.com"}

THREE_LABELS = """# Synthetic Three-Way Sync

## Transcript

**SPEAKER_00** _[00:01]_
Hi both, thanks for joining. This is Shaw.

**SPEAKER_01** _[00:06]_
Thanks Shaw, Dana here.

**SPEAKER_02** _[00:10]_
And this is Sam Ortiz from Example Labs.

**SPEAKER_01** _[01:00]_
I will share the API credentials with you tomorrow morning.

**SPEAKER_02** _[02:00]_
I will send the integration checklist to you on Monday.

**SPEAKER_00** _[03:00]_
I'll send you the pricing deck by Friday.
"""


@pytest.fixture
def call(fixture_dir):
    # SPEAKER_00 = Shaw, SPEAKER_01 = Dana Reyes; 2026-11-24T19:00:00Z, 600 s.
    return sources.load_pocket(os.path.join(fixture_dir, "pocket", "two-party-call"))


@pytest.fixture
def call3(call):
    return dataclasses.replace(call, transcript=THREE_LABELS)


def _raw(*names, shaw_label="SPEAKER_00"):
    raw = {"shaw_label": shaw_label, "shaw_label_evidence": "This is Shaw.", "items": []}
    if names:
        raw["speaker_names"] = [{"label": l, "name": n, "evidence": e} for l, n, e in names]
    return raw


DANA_EVIDENCED = ("SPEAKER_01", "Dana Reyes", "Hi Dana, thanks for making time.")


def _item(owner, speaker, **fields):
    base = {"owner": owner, "counterpart": "someone", "quote": "q"}
    base.update(fields)
    return extract.Item(owner=owner, quote="q", span=(0, 1), citation="pocket:x@00:00",
                        speaker=speaker, trust="inferred", fields=base)


# --- calendar match and invited set -------------------------------------------------------------

def test_event_match_gives_invited_set_minus_shaw_and_declined(call):
    roster = participants.resolve(call, _raw(), ONE_INVITEE, shaw_emails=SHAW)
    assert roster.event is not None and roster.event.uid == "sync-1@example.com"
    invited = [(p.name, p.email) for p in roster.participants]
    assert invited == [("Dana Reyes", "dana@example.com")]      # no Shaw, no declined Lee


def test_no_event_means_no_invited_set(call):
    roster = participants.resolve(call, _raw(DANA_EVIDENCED), [], shaw_emails=SHAW)
    assert roster.event is None
    assert roster.label_names == {} and roster.verified_names == set()
    assert participants.with_line(roster) == ["unidentified"]


# --- label -> name mappings ---------------------------------------------------------------------

def test_evidenced_mapping_to_invitee_is_accepted_and_verified(call):
    roster = participants.resolve(call, _raw(DANA_EVIDENCED), TWO_INVITEES, shaw_emails=SHAW)
    assert roster.label_names == {"SPEAKER_01": "Dana Reyes"}
    assert roster.verified_names == {"Dana Reyes"}
    dana = [p for p in roster.participants if p.name == "Dana Reyes"][0]
    assert (dana.label, dana.status, dana.email) == ("SPEAKER_01", "spoke", "dana@example.com")


def test_mapping_name_match_is_case_insensitive(call):
    raw = _raw(("SPEAKER_01", "dana REYES", "Hi Dana, thanks for making time."))
    roster = participants.resolve(call, raw, TWO_INVITEES, shaw_emails=SHAW)
    assert roster.label_names == {"SPEAKER_01": "Dana Reyes"}   # canonical name from the invite


def test_mapping_with_evidence_not_in_transcript_is_rejected(call):
    raw = _raw(("SPEAKER_01", "Dana Reyes", "Dana here, head of partnerships."))
    roster = participants.resolve(call, raw, TWO_INVITEES, shaw_emails=SHAW)
    assert roster.label_names == {}
    assert roster.verified_names == set()
    assert {p.status for p in roster.participants} == {"invited"}


def test_mapping_to_name_not_invited_or_contact_is_rejected(call):
    raw = _raw(("SPEAKER_01", "Dana Smith", "Hi Dana, thanks for making time."))
    roster = participants.resolve(call, raw, TWO_INVITEES, shaw_emails=SHAW, contacts=[SAM_CONTACT])
    assert roster.label_names == {}
    assert roster.verified_names == set()


def test_mapping_to_contact_is_accepted_but_unverified(call3):
    raw = _raw(("SPEAKER_02", "sammy", "And this is Sam Ortiz from Example Labs."))
    roster = participants.resolve(call3, raw, TWO_INVITEES, shaw_emails=SHAW, contacts=[SAM_CONTACT])
    assert roster.label_names == {"SPEAKER_02": "Sam Ortiz"}    # alias resolved to the contact name
    assert roster.verified_names == set()                       # a contact is not an invitee


def test_shaws_own_label_is_never_mapped_to_a_participant(call):
    raw = _raw(("SPEAKER_00", "Dana Reyes", "Hi Dana, thanks for making time."))
    roster = participants.resolve(call, raw, TWO_INVITEES, shaw_emails=SHAW)
    assert roster.label_names == {}


# --- two-party shortcut -------------------------------------------------------------------------

def test_two_party_shortcut_maps_other_label_to_sole_invitee(call):
    roster = participants.resolve(call, _raw(), ONE_INVITEE, shaw_emails=SHAW)
    assert roster.label_names == {"SPEAKER_01": "Dana Reyes"}
    assert roster.verified_names == {"Dana Reyes"}


def test_shortcut_not_applied_with_three_labels(call3):
    roster = participants.resolve(call3, _raw(), ONE_INVITEE, shaw_emails=SHAW)
    assert roster.label_names == {}
    assert roster.verified_names == set()


def test_shortcut_not_applied_with_two_invitees(call):
    roster = participants.resolve(call, _raw(), TWO_INVITEES, shaw_emails=SHAW)
    assert roster.label_names == {}


def test_shortcut_needs_shaw_label_in_transcript(call):
    roster = participants.resolve(call, _raw(shaw_label="SPEAKER_07"), ONE_INVITEE, shaw_emails=SHAW)
    assert roster.label_names == {}


def test_odd_input_never_raises(call):
    for raw in (None, {}, {"speaker_names": "nope"}, {"speaker_names": [None, 3, {"label": 1}]}):
        roster = participants.resolve(call, raw, ONE_INVITEE, shaw_emails=SHAW, contacts=[None, {}])
        assert isinstance(roster.label_names, dict)
    bad = dataclasses.replace(call, created_at="not a date")
    assert participants.resolve(bad, _raw(), ONE_INVITEE, shaw_emails=SHAW).event is None


# --- With: line ---------------------------------------------------------------------------------

def test_with_line_renders_all_three_statuses(call3):
    raw = _raw(("SPEAKER_01", "Dana Reyes", "Thanks Shaw, Dana here."),
               ("SPEAKER_02", "Sam Ortiz", "And this is Sam Ortiz from Example Labs."))
    roster = participants.resolve(call3, raw, TWO_INVITEES, shaw_emails=SHAW, contacts=[SAM_CONTACT])
    assert participants.with_line(roster) == ["Dana Reyes ✓", "Sam Ortiz (unverified)",
                                              "Pat Quinn (invited)"]


def test_with_line_empty_is_unidentified(call):
    roster = participants.resolve(call, _raw(), [], shaw_emails=SHAW)
    assert participants.with_line(roster) == ["unidentified"]


# --- trust --------------------------------------------------------------------------------------

def test_apply_trust_upgrades_participant_item_only_when_verified(call3):
    raw = _raw(("SPEAKER_01", "Dana Reyes", "Thanks Shaw, Dana here."),
               ("SPEAKER_02", "Sam Ortiz", "And this is Sam Ortiz from Example Labs."))
    roster = participants.resolve(call3, raw, TWO_INVITEES, shaw_emails=SHAW, contacts=[SAM_CONTACT])
    dana, sam = participants.apply_trust(
        [_item("participant", "SPEAKER_01"), _item("participant", "SPEAKER_02")], roster)
    assert dana.trust == "verified"
    assert sam.trust == "inferred"                               # mapped, but only to a contact


def test_counterpart_rewritten_to_mapped_name(call3):
    raw = _raw(("SPEAKER_02", "Sam Ortiz", "And this is Sam Ortiz from Example Labs."))
    roster = participants.resolve(call3, raw, TWO_INVITEES, shaw_emails=SHAW, contacts=[SAM_CONTACT])
    sam, unmapped = participants.apply_trust(
        [_item("participant", "SPEAKER_02"), _item("participant", "SPEAKER_01")], roster)
    assert sam.fields["counterpart"] == "Sam Ortiz"
    assert unmapped.fields["counterpart"] == "someone"          # left alone when not mapped


def test_shaw_items_stay_inferred_without_matched_event(call):
    roster = participants.resolve(call, _raw(DANA_EVIDENCED), [], shaw_emails=SHAW)
    (item,) = participants.apply_trust([_item("shaw", "SPEAKER_00")], roster)
    assert item.trust == "inferred"


def test_shaw_item_verified_when_every_other_label_is_a_verified_invitee(call):
    roster = participants.resolve(call, _raw(), ONE_INVITEE, shaw_emails=SHAW)   # shortcut
    (item,) = participants.apply_trust([_item("shaw", "SPEAKER_00")], roster)
    assert item.trust == "verified"


def test_shaw_item_stays_inferred_when_another_label_is_unverified(call3):
    raw = _raw(("SPEAKER_01", "Dana Reyes", "Thanks Shaw, Dana here."),
               ("SPEAKER_02", "Sam Ortiz", "And this is Sam Ortiz from Example Labs."))
    roster = participants.resolve(call3, raw, TWO_INVITEES, shaw_emails=SHAW, contacts=[SAM_CONTACT])
    (item,) = participants.apply_trust([_item("shaw", "SPEAKER_00")], roster)
    assert item.trust == "inferred"


def test_inputs_not_mutated(call):
    raw = _raw(DANA_EVIDENCED)
    raw_before = copy.deepcopy(raw)
    roster = participants.resolve(call, raw, TWO_INVITEES, shaw_emails=SHAW)
    items = [_item("participant", "SPEAKER_01")]
    items_before = copy.deepcopy(items)
    out = participants.apply_trust(items, roster)
    assert raw == raw_before
    assert items == items_before                                 # trust and fields untouched
    assert out[0] is not items[0] and out[0].fields is not items[0].fields


# --- next call (design §3 rule 3, email only) ---------------------------------------------------

FUTURE = (
    # Cancelled and declined meetings with Dana come first; neither may be chosen.
    _vevent("cancelled-1@example.com", Z(2026, 11, 27, 19, 0), Z(2026, 11, 27, 20, 0), "Cancelled sync",
            [SHAW_ATT, DANA_ATT], status="CANCELLED"),
    _vevent("declined-1@example.com", Z(2026, 11, 28, 19, 0), Z(2026, 11, 28, 20, 0), "Declined sync",
            [SHAW_ATT, ("Dana Reyes", "dana@example.com", "DECLINED")]),
    _vevent("next-dana-0001@example.com", Z(2026, 12, 1, 19, 0), Z(2026, 12, 1, 20, 0), "Pilot check-in",
            [SHAW_ATT, DANA_ATT]),
    # Someone known only by name: a CN on a later invite must never stand in for an email.
    _vevent("morgan-1@example.com", Z(2026, 12, 2, 19, 0), Z(2026, 12, 2, 20, 0), "Morgan intro",
            [SHAW_ATT, ("Morgan Lee", "morgan@example.com", "ACCEPTED")]),
)


def test_next_call_due_by_email_finds_next_event_and_formats_due(call):
    events = _calendar(_call_event(SHAW_ATT, DANA_ATT), *FUTURE)
    roster = participants.resolve(call, _raw(), events, shaw_emails=SHAW)
    due = participants.next_call_due("Dana Reyes", roster, events, (), sources.call_end(call), TZ)
    # 2026-12-01 19:00Z is 14:00 EST; one hour before is 13:00 New York time.
    assert due.due_ts == "2026-12-01T13:00:00-05:00"
    assert due.due_source == ("next call with Dana Reyes (Pilot check-in, 2026-12-01, "
                              "uid next-dan)")


def test_next_call_due_email_from_contact(call):
    events = _calendar(*FUTURE)                                   # no event matches the call
    roster = participants.resolve(call, _raw(), events, shaw_emails=SHAW)
    contacts = [{"name": "Dana Reyes", "aliases": ["Dana"], "email": "dana@example.com"}]
    due = participants.next_call_due("dana", roster, events, contacts, sources.call_end(call), TZ)
    assert due is not None and due.due_ts.startswith("2026-12-01T13:00")


def test_next_call_due_no_email_is_none(call):
    events = _calendar(_call_event(SHAW_ATT, DANA_ATT), *FUTURE)
    roster = participants.resolve(call, _raw(), events, shaw_emails=SHAW)
    # Morgan Lee is a CN on a later event but neither invited to this call nor a contact.
    assert participants.next_call_due("Morgan Lee", roster, events, (), sources.call_end(call), TZ) is None


def test_next_call_due_none_when_no_later_event(call):
    events = _calendar(_call_event(SHAW_ATT, DANA_ATT))
    roster = participants.resolve(call, _raw(), events, shaw_emails=SHAW)
    assert participants.next_call_due("Dana Reyes", roster, events, (), sources.call_end(call), TZ) is None


def test_next_call_due_skips_cancelled_and_declined(call):
    # Only the cancelled and declined meetings exist after the call: nothing qualifies.
    events = _calendar(_call_event(SHAW_ATT, DANA_ATT), *FUTURE[:2])
    roster = participants.resolve(call, _raw(), events, shaw_emails=SHAW)
    assert participants.next_call_due("Dana Reyes", roster, events, (), sources.call_end(call), TZ) is None
