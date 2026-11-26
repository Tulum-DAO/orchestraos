"""Calendar ICS reader (design §2a/§3): which event a call belongs to, who was invited, and the next
meeting with a counterpart. All fixture data is synthetic (example.com addresses, made-up names)."""
import datetime as dt
import os

import pytest

from postcall import calendar_ics as cal

from ._ics import Z, fill

UTC = dt.timezone.utc


def utc(*a):
    return dt.datetime(*a, tzinfo=UTC)


@pytest.fixture
def events(fixture_dir, tmp_path):
    """The fixture calendar, its {Z:...} UTC placeholders filled in at test time."""
    with open(os.path.join(fixture_dir, "calendar.ics.tmpl"), newline="") as f:
        text = fill(f.read())
    path = tmp_path / "calendar.ics"
    path.write_bytes(text.encode())
    return cal.load(str(path))


def by_uid(evs, uid):
    return [e for e in evs if e.uid == uid]


def one(evs, uid):
    found = by_uid(evs, uid)
    assert len(found) == 1, f"{uid}: {found}"
    return found[0]


# --- parse --------------------------------------------------------------------------------------

def test_unfolding_params_and_quoted_cn():
    # CRLF line endings, a fold inside the mailto value and a fold with a TAB continuation.
    text = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1@example.com\r\n"
            f"DTSTART:{Z(2026, 11, 24, 19, 0)}\r\nDTEND:{Z(2026, 11, 24, 20, 0)}\r\n"
            "SUMMARY:Long\r\n\t title\r\n"
            'ATTENDEE;CN="Reyes, Dana: CEO";PARTSTAT=accepted:mailto:Dana\r\n @Example.com\r\n'
            "ATTENDEE;CN=Pat Quinn:MAILTO:pat@example.com\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n")
    ev = one(cal.parse(text), "u1@example.com")
    assert ev.summary == "Long title"
    assert ev.attendees == (cal.Attendee("dana@example.com", "Reyes, Dana: CEO", "ACCEPTED"),
                            cal.Attendee("pat@example.com", "Pat Quinn", "NEEDS-ACTION"))


def test_fixture_fold_and_params(events):
    ev = one(events, "call-1@example.com")
    assert ev.summary == "Quarterly review, Acme Widgets"      # \, unescaped
    emails = [a.email for a in ev.attendees]
    assert "dana@example.com" in emails                        # folded across lines
    dana = [a for a in ev.attendees if a.email == "dana@example.com"][0]
    assert (dana.cn, dana.partstat) == ("Reyes, Dana", "ACCEPTED")


def test_tzid_converted_to_utc(events):
    ev = one(events, "call-1@example.com")
    assert ev.start == utc(2026, 11, 24, 19, 0)                # 14:00 New York (EST, UTC-5)
    assert ev.end == utc(2026, 11, 24, 20, 0)
    assert ev.start.utcoffset() == dt.timedelta(0)


def test_z_floating_and_all_day(events):
    assert one(events, "decoy-1@example.com").start == utc(2026, 11, 24, 19, 45)
    floating = one(events, "floating-1@example.com")
    assert (floating.start, floating.end) == (utc(2026, 11, 28, 9), utc(2026, 11, 28, 10))
    allday = one(events, "allday-1@example.com")
    assert (allday.start, allday.end) == (utc(2026, 11, 26), utc(2026, 11, 27))
    assert allday.all_day is True


def test_duration_and_missing_end(events):
    dur = one(events, "dur-1@example.com")
    assert dur.end - dur.start == dt.timedelta(hours=1, minutes=30)
    nodur = one(events, "nodur-1@example.com")
    assert nodur.end - nodur.start == dt.timedelta(hours=1)


def test_parse_keeps_masters_and_overrides_unexpanded(events):
    series = by_uid(events, "weekly-1@example.com")
    assert sorted((e.recurrence_id or "") for e in series) == ["", Z(2026, 12, 14, 15, 0), Z(2026, 12, 16, 15, 0)]


def test_malformed_text_does_not_raise():
    text = ("garbage line without colon\n"
            "BEGIN:VEVENT\nUID:bad-date@example.com\nDTSTART:2026-11-24 nope\nEND:VEVENT\n"
            "BEGIN:VEVENT\nUID:no-start@example.com\nSUMMARY:x\nEND:VEVENT\n"
            "BEGIN:VEVENT\nUID:bad-tz@example.com\nDTSTART;TZID=Mars/Olympus:20261124T100000\nEND:VEVENT\n"
            f"BEGIN:VEVENT\nUID:bad-rule@example.com\nDTSTART:{Z(2026, 11, 24, 10, 0)}\n"
            "RRULE:FREQ=WEEKLY;INTERVAL=zero;BYDAY=XX\nDURATION:Pwhat\nEND:VEVENT\n"
            f"BEGIN:VEVENT\nUID:good@example.com\nDTSTART:{Z(2026, 11, 25, 10, 0)}\nEND:VEVENT\n"
            f"BEGIN:VEVENT\nUID:unterminated@example.com\nDTSTART:{Z(2026, 11, 26, 10, 0)}\n")
    evs = cal.parse(text)
    assert "good@example.com" in [e.uid for e in evs]
    assert "bad-date@example.com" not in [e.uid for e in evs]
    cal.expand(evs, utc(2026, 11, 1), utc(2027, 1, 1))         # must not raise either
    assert cal.parse("") == [] and cal.parse(None) == []       # type: ignore[arg-type]


def test_load_missing_file_returns_empty(tmp_path):
    assert cal.load(str(tmp_path / "nope.ics")) == []
    assert cal.load(str(tmp_path)) == []                       # a directory is unreadable as a file


# --- expand -------------------------------------------------------------------------------------

DEC = (utc(2026, 12, 1), utc(2027, 1, 1))


def starts(evs, uid):
    return [e.start for e in evs if e.uid == uid]


def test_weekly_byday_count(events):
    # 6 instances on MO/WE from Wed Dec 2; Dec 9 EXDATE'd, Dec 14 moved, Dec 16 cancelled.
    got = starts(cal.expand(events, *DEC), "weekly-1@example.com")
    assert got == [utc(2026, 12, 2, 15), utc(2026, 12, 7, 15), utc(2026, 12, 15, 21), utc(2026, 12, 21, 15)]


def test_until_inclusive_with_interval(events):
    got = starts(cal.expand(events, *DEC), "daily-1@example.com")
    assert got == [utc(2026, 12, d, 13) for d in (1, 3, 5, 7)]


def test_monthly_bymonthday(events):
    got = starts(cal.expand(events, utc(2026, 12, 1), utc(2027, 6, 1)), "monthly-1@example.com")
    assert got == [utc(2026, 12, 15, 14), utc(2027, 1, 15, 14), utc(2027, 2, 15, 14)]


def test_monthly_nth_weekday():
    # "First Tuesday" and "last Friday" are how people actually book monthly calls.
    text = (f"BEGIN:VEVENT\nUID:first-tu@example.com\nDTSTART:{Z(2026, 12, 1, 15, 0)}\n"
            "RRULE:FREQ=MONTHLY;BYDAY=1TU;COUNT=3\nEND:VEVENT\n"
            f"BEGIN:VEVENT\nUID:last-fr@example.com\nDTSTART:{Z(2026, 12, 25, 15, 0)}\n"
            "RRULE:FREQ=MONTHLY;BYDAY=-1FR;COUNT=2\nEND:VEVENT\n")
    expanded = cal.expand(cal.parse(text), utc(2026, 12, 1), utc(2027, 6, 1))
    assert starts(expanded, "first-tu@example.com") == [utc(2026, 12, 1, 15), utc(2027, 1, 5, 15), utc(2027, 2, 2, 15)]
    assert starts(expanded, "last-fr@example.com") == [utc(2026, 12, 25, 15), utc(2027, 1, 29, 15)]


def test_exdate_removes_instance(events):
    got = starts(cal.expand(events, *DEC), "weekly-1@example.com")
    assert utc(2026, 12, 9, 15) not in got
    # A comma list of EXDATEs in UTC works too.
    text = (f"BEGIN:VEVENT\nUID:x@example.com\nDTSTART:{Z(2026, 12, 1, 10, 0)}\nRRULE:FREQ=DAILY;COUNT=4\n"
            f"EXDATE:{Z(2026, 12, 2, 10, 0)},{Z(2026, 12, 3, 10, 0)}\nEND:VEVENT\n")
    assert starts(cal.expand(cal.parse(text), *DEC), "x@example.com") == [utc(2026, 12, 1, 10), utc(2026, 12, 4, 10)]


def test_override_moves_instance(events):
    expanded = cal.expand(events, *DEC)
    got = starts(expanded, "weekly-1@example.com")
    assert utc(2026, 12, 14, 15) not in got                    # original slot is gone
    moved = [e for e in expanded if e.start == utc(2026, 12, 15, 21)]
    assert len(moved) == 1
    assert moved[0].summary == "Weekly sync (moved)" and moved[0].recurrence_id == Z(2026, 12, 14, 15, 0)


def test_cancelled_master_and_cancelled_override_excluded(events):
    expanded = cal.expand(events, utc(2026, 11, 1), utc(2027, 1, 1))
    assert "cancel-1@example.com" not in [e.uid for e in expanded]
    assert utc(2026, 12, 16, 15) not in starts(expanded, "weekly-1@example.com")
    assert all(e.status != "CANCELLED" for e in expanded)


def test_weekly_across_dst_keeps_local_clock(events):
    got = starts(cal.expand(events, utc(2027, 2, 1), utc(2027, 4, 1)), "dst-1@example.com")
    # 10:00 New York throughout: EST (UTC-5) before 2027-03-14, EDT (UTC-4) after.
    assert got == [utc(2027, 3, 1, 15), utc(2027, 3, 8, 15), utc(2027, 3, 15, 14), utc(2027, 3, 22, 14)]


def test_expand_window_and_sorting(events):
    expanded = cal.expand(events, utc(2026, 11, 24, 19, 30), utc(2026, 11, 24, 19, 50))
    assert [e.uid for e in expanded] == ["call-1@example.com", "decoy-1@example.com"]
    full = cal.expand(events, utc(2026, 11, 1), utc(2027, 6, 1))
    assert [e.start for e in full] == sorted(e.start for e in full)


# --- match_call ---------------------------------------------------------------------------------

def test_match_call_picks_larger_overlap(events):
    ev = cal.match_call(events, utc(2026, 11, 24, 19, 2), utc(2026, 11, 24, 19, 58))
    assert ev is not None and ev.uid == "call-1@example.com"   # decoy overlaps only 13 min


def test_match_call_uses_slack_when_no_true_overlap(events):
    # Recording starts 8 minutes after the 90-minute DURATION event ended.
    ev = cal.match_call(events, utc(2026, 11, 30, 18, 38), utc(2026, 11, 30, 19, 0))
    assert ev is not None and ev.uid == "dur-1@example.com"


def _two(a, b):
    return cal.parse("".join(
        f"BEGIN:VEVENT\nUID:{uid}\nDTSTART:{s}\nDTEND:{e}\nEND:VEVENT\n" for uid, s, e in (a, b)))


def test_match_call_tie_is_ambiguous():
    evs = _two(("a@example.com", Z(2026, 11, 24, 19, 0), Z(2026, 11, 24, 20, 0)),
               ("b@example.com", Z(2026, 11, 24, 19, 0), Z(2026, 11, 24, 20, 0)))
    assert cal.match_call(evs, utc(2026, 11, 24, 19), utc(2026, 11, 24, 20)) is None


def test_match_call_no_event(events):
    assert cal.match_call(events, utc(2026, 11, 25, 3), utc(2026, 11, 25, 4)) is None
    assert cal.match_call([], utc(2026, 11, 25, 3), utc(2026, 11, 25, 4)) is None


def test_match_call_ignores_all_day_events(events):
    # Nov 26 has only the all-day holiday and a cancelled event: no match, not a false positive.
    assert cal.match_call(events, utc(2026, 11, 26, 19), utc(2026, 11, 26, 20)) is None


# --- invited / next_with_attendee ---------------------------------------------------------------

def test_invited_drops_shaw_declined_and_rooms(events):
    ev = one(events, "call-1@example.com")
    people = cal.invited(ev, {"SHAW@example.com"})
    assert [a.email for a in people] == ["dana@example.com", "pat@example.com"]


AFTER_CALL = utc(2026, 11, 24, 20)


def test_next_with_attendee_skips_cancelled_and_declined(events):
    # Nov 26 is cancelled, Nov 27 Dana declined: the next real meeting is the Dec 2 weekly sync.
    ev = cal.next_with_attendee(events, "DANA@example.com", AFTER_CALL)
    assert ev is not None and (ev.uid, ev.start) == ("weekly-1@example.com", utc(2026, 12, 2, 15))


def test_next_with_attendee_respects_horizon(events):
    assert cal.next_with_attendee(events, "quinn@example.com", AFTER_CALL) is None     # 92 days out
    ev = cal.next_with_attendee(events, "quinn@example.com", AFTER_CALL, horizon=dt.timedelta(days=100))
    assert ev is not None and ev.uid == "far-1@example.com"


def test_next_with_attendee_strictly_after(events):
    # Asking from the moment the Dec 2 instance starts skips it and finds the Dec 7 instance.
    ev = cal.next_with_attendee(events, "dana@example.com", utc(2026, 12, 2, 15))
    assert ev is not None and ev.start == utc(2026, 12, 7, 15)
