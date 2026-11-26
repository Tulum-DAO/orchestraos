"""Deadlines (design §3): explicit > relative > (calendar, later increment) > unknown. Unknown stays
unknown and says so; the zone is Shaw's zone at the call, never a hardcoded ET."""
import pytest

from postcall import deadlines

CALL = "2026-11-24T19:00:00Z"          # Tuesday, 14:00 local (UTC-5, no DST)
LOCAL_TZ = "America/Cancun"


def due(phrase, anchor=CALL, tz=LOCAL_TZ, **kw):
    return deadlines.resolve(phrase, anchor, tz, **kw)


@pytest.mark.parametrize("phrase,expected", [
    ("by Friday", "2026-11-27T17:00:00-05:00"),
    ("Friday at 2pm", "2026-11-27T14:00:00-05:00"),
    ("before Monday", "2026-11-30T09:00:00-05:00"),
    ("tomorrow", "2026-11-25T17:00:00-05:00"),
    ("tomorrow morning", "2026-11-25T09:00:00-05:00"),
    ("tomorrow at 3", "2026-11-25T15:00:00-05:00"),
    ("tomorrow at 10:30am", "2026-11-25T10:30:00-05:00"),
    ("tonight", "2026-11-24T21:00:00-05:00"),
    ("in 3 days", "2026-11-27T17:00:00-05:00"),
    ("Dec 3", "2026-12-03T17:00:00-05:00"),
    ("December 3rd at 10am", "2026-12-03T10:00:00-05:00"),
    ("by January 5", "2027-01-05T17:00:00-05:00"),
])
def test_resolves_in_shaws_zone(phrase, expected):
    d = due(phrase)
    assert d.due_ts == expected
    assert d.due_source == phrase


def test_bare_day_of_month_is_ambiguous_and_terminal():
    d = due("the 3rd")
    assert d.due_ts is None
    assert d.due_source == "ambiguous: the 3rd"


@pytest.mark.parametrize("phrase", ["later", "when he gets back", "before then", "-"])
def test_unresolvable_phrase_stays_unknown(phrase):
    d = due(phrase)
    assert d.due_ts is None
    assert d.due_source == f"unknown ({phrase})"


def test_no_phrase_is_none_stated():
    assert due("") == deadlines.Due(None, "none stated")
    assert due(None) == deadlines.Due(None, "none stated")


def test_zone_at_call_uses_current_zone_when_set_before_the_call():
    loc = {"tz": LOCAL_TZ, "label": "Local", "since": "2026-11-22",
           "home_base": {"tz": LOCAL_TZ, "label": "Local"}}
    assert deadlines.zone_at_call(CALL, loc) == (LOCAL_TZ, "Local", False)


def test_zone_at_call_falls_back_to_home_base_when_he_moved_after_the_call():
    loc = {"tz": "America/New_York", "label": "ET", "since": "2026-11-26",
           "home_base": {"tz": LOCAL_TZ, "label": "Local"}}
    assert deadlines.zone_at_call(CALL, loc) == (LOCAL_TZ, "Local", True)


def test_assumed_zone_keeps_the_date_drops_the_clock_and_says_so():
    d = due("Friday at 2pm", zone_assumed="Local")
    assert d.due_ts == "2026-11-27T17:00:00-05:00"
    assert d.due_source == "Friday at 2pm (zone assumed: Local)"


def test_nonexistent_dst_wall_time_moves_forward_and_is_marked():
    # 2027-03-14 is the US spring-forward Sunday: 02:30 does not exist in New York.
    d = due("Sunday at 2:30am", anchor="2027-03-10T15:00:00Z", tz="America/New_York")
    assert d.due_ts == "2027-03-14T03:00:00-04:00"
    assert d.due_source == "Sunday at 2:30am (DST-adjusted)"


def test_ambiguous_dst_wall_time_takes_the_first_occurrence_and_is_marked():
    # 2027-11-07 is the US fall-back Sunday: 01:30 happens twice in New York.
    d = due("Sunday at 1:30am", anchor="2027-11-03T15:00:00Z", tz="America/New_York")
    assert d.due_ts == "2027-11-07T01:30:00-04:00"
    assert d.due_source == "Sunday at 1:30am (DST-adjusted)"


def test_local_zone_is_not_new_york_in_summer():
    # June: New York is UTC-4 (EDT), the local zone stays UTC-5. A hardcoded ET would be an hour off.
    d = due("tomorrow", anchor="2027-06-15T15:00:00Z")
    assert d.due_ts == "2027-06-16T17:00:00-05:00"


# --- dry-run gaps (gm msg_feff515d): spelled-out clocks, a named zone, relative hours -------------

@pytest.mark.parametrize("phrase,expected", [
    ("two PM EST", "2026-11-24T14:00:00-05:00"),              # ET = UTC-5 on this date
    ("at four pm", "2026-11-24T16:00:00-05:00"),
    ("by 5 p.m.", "2026-11-24T17:00:00-05:00"),
    ("tomorrow at eleven am", "2026-11-25T11:00:00-05:00"),
    ("three o'clock tomorrow", "2026-11-25T15:00:00-05:00"),
])
def test_spelled_out_and_bare_clock_times_resolve(phrase, expected):
    d = due(phrase, anchor="2026-11-24T18:00:00Z")            # 13:00 local: these times are still ahead
    assert d.due_ts == expected


def test_a_clock_time_already_past_on_the_call_day_is_the_next_day():
    d = due("at 9am", anchor="2026-11-24T18:00:00Z")
    assert d.due_ts == "2026-11-25T09:00:00-05:00"


def test_a_named_zone_is_honoured_over_shaws_zone():
    d = due("2pm Pacific", anchor="2026-11-24T18:00:00Z")
    assert d.due_ts == "2026-11-24T17:00:00-05:00"            # 14:00 PT = 17:00 in the test zone (UTC-5)
    assert "Pacific" in d.due_source


@pytest.mark.parametrize("phrase,hours", [
    ("in the next couple of hours", 2), ("in a couple hours", 2), ("in an hour", 1),
    ("within 3 hours", 3), ("in the next few hours", 3),
])
def test_relative_hours_count_from_the_call(phrase, hours):
    d = due(phrase)
    import datetime as dt
    expected = (dt.datetime(2026, 11, 24, 19, tzinfo=dt.timezone.utc) + dt.timedelta(hours=hours))
    assert deadlines._parse_utc(d.due_ts) == expected
    assert d.due_source == phrase


def test_conditions_without_a_time_stay_unknown():
    for phrase in ("once I'm at my desk", "in the meantime", "whenever you're back at your desk"):
        assert due(phrase).due_ts is None


# --- codex gate on #655: never a confidently wrong date -------------------------------------------

@pytest.mark.parametrize("phrase", ["next month at 2pm", "in two days at 4pm", "after the launch at 3pm",
                                    "half past two pm", "quarter to five pm", "in an hour and a half",
                                    "tomorrow at half past two pm", "friday at quarter to five pm",
                                    "in 2 hours and 20 minutes"])
def test_a_partly_understood_phrase_stays_unknown(phrase):
    d = due(phrase, anchor="2026-11-24T18:00:00Z")
    assert d.due_ts is None, d


def test_a_standard_time_abbreviation_named_during_daylight_time_is_ambiguous():
    d = due("tomorrow at 2pm EST", anchor="2027-06-15T15:00:00Z")
    assert d.due_ts is None and d.due_source.startswith("ambiguous")


def test_a_matching_abbreviation_and_season_resolves():
    d = due("tomorrow at 2pm EDT", anchor="2027-06-15T15:00:00Z")
    assert deadlines._parse_utc(d.due_ts).isoformat() == "2027-06-16T18:00:00+00:00"


def test_the_next_occurrence_counts_the_second_fall_back_hour():
    # New York falls back at 06:00Z on 2027-11-07; 06:15Z is 01:15 EST, so 01:30 is 15 min away.
    d = due("at 1:30am", anchor="2027-11-07T06:15:00Z", tz="America/New_York")
    assert deadlines._parse_utc(d.due_ts).isoformat() == "2027-11-07T06:30:00+00:00"


# --- codex gate round 2: every number and clock in the phrase must be used ------------------------

@pytest.mark.parametrize("phrase", ["in 2 hours 30 minutes", "in 2 hours, and 20 minutes", "at 2pm or 4pm",
                                    "between 2pm and 4pm", "tomorrow at two twenty-five pm", "friday 2 or 3pm",
                                    "in an hour or two", "tomorrow, 10 or 11am"])
def test_a_phrase_with_an_unused_number_or_a_second_clock_stays_unknown(phrase):
    d = due(phrase, anchor="2026-11-24T18:00:00Z")
    assert d.due_ts is None, d


@pytest.mark.parametrize("phrase,expected", [
    ("by Friday", "2026-11-27T17:00:00-05:00"), ("Dec 3", "2026-12-03T17:00:00-05:00"),
    ("in 3 days", "2026-11-27T17:00:00-05:00"), ("in a week or two", "2026-12-08T17:00:00-05:00"),
    ("tomorrow at 10:30am", "2026-11-25T10:30:00-05:00"),
])
def test_numbers_the_parser_reads_are_still_used(phrase, expected):
    assert due(phrase).due_ts == expected


# --- codex gate round 3: the new paths read the WHOLE phrase or nothing ---------------------------

@pytest.mark.parametrize("phrase", ["in 2 hours, and a day", "in an hour after the next call", "at 2pm EST or PST",
                                    "in a couple hours or tomorrow", "within 3 hours unless it slips",
                                    "at 2pm eastern or central"])
def test_relative_hours_and_zones_must_be_the_whole_phrase(phrase):
    assert due(phrase, anchor="2026-11-24T18:00:00Z").due_ts is None


def test_a_morning_qualifier_beats_the_afternoon_heuristic():
    assert due("tomorrow at two o'clock in the morning").due_ts == "2026-11-25T02:00:00-05:00"
    assert due("tomorrow at 3 in the afternoon").due_ts == "2026-11-25T15:00:00-05:00"
    assert due("tomorrow at 8 in the evening").due_ts == "2026-11-25T20:00:00-05:00"


def test_spelled_standard_and_daylight_time_are_read():
    d = due("tomorrow at 2pm Eastern Standard Time", anchor="2027-06-15T15:00:00Z")
    assert d.due_ts is None and d.due_source.startswith("ambiguous")
    d = due("tomorrow at 2pm Eastern Daylight Time", anchor="2027-06-15T15:00:00Z")
    assert deadlines._parse_utc(d.due_ts).isoformat() == "2027-06-16T18:00:00+00:00"


# --- codex gate round 4: one zone grammar everywhere; clock-only qualifiers; bounded hours ---------

@pytest.mark.parametrize("phrase,anchor,expected", [
    ("at 2pm Eastern Standard", "2027-06-15T15:00:00Z", None),                 # summer: ambiguous
    ("at 2pm Eastern Standard Time", "2027-06-15T15:00:00Z", None),
    ("at 2pm Eastern Daylight", "2026-11-24T15:00:00Z", None),                  # winter: ambiguous
    ("at 2pm Eastern Daylight", "2027-06-15T15:00:00Z", "2027-06-15T18:00:00+00:00"),
    ("at 2pm Eastern Standard", "2026-11-24T15:00:00Z", "2026-11-24T19:00:00+00:00"),
])
def test_standard_and_daylight_are_read_with_or_without_time(phrase, anchor, expected):
    d = due(phrase, anchor=anchor)
    got = deadlines._parse_utc(d.due_ts).isoformat() if d.due_ts else None
    assert got == expected, d


def test_a_clock_only_phrase_with_a_part_of_day_qualifier_resolves():
    assert due("at 3 in the morning", anchor="2026-11-24T18:00:00Z").due_ts == "2026-11-25T03:00:00-05:00"
    assert due("at 8 in the evening", anchor="2026-11-24T18:00:00Z").due_ts == "2026-11-24T20:00:00-05:00"


@pytest.mark.parametrize("phrase", ["in 999999999999999 hours", "in 100 hours"])
def test_relative_hours_are_bounded(phrase):
    assert due(phrase).due_ts is None


# --- codex gate round 5: midnight is never noon; hour counts are parsed whole ---------------------

@pytest.mark.parametrize("phrase", ["at 12 at night", "at twelve o'clock at night", "tomorrow at 12 at night",
                                    "at 12 in the evening", "tonight at 12"])
def test_twelve_at_night_is_ambiguous_never_noon(phrase):
    d = due(phrase, anchor="2026-11-24T18:00:00Z", tz="America/New_York")
    assert d.due_ts is None and d.due_source.startswith("ambiguous"), d


def test_twelve_in_the_morning_is_midnight_starting_the_day():
    assert due("tomorrow at 12 in the morning").due_ts == "2026-11-25T00:00:00-05:00"


@pytest.mark.parametrize("phrase,hours", [("in 0000002 hours", 2), ("in 02 hours", 2)])
def test_leading_zeros_are_read_whole(phrase, hours):
    import datetime as dt
    d = due(phrase)
    assert deadlines._parse_utc(d.due_ts) == dt.datetime(2026, 11, 24, 19, tzinfo=dt.timezone.utc) + dt.timedelta(hours=hours)


# --- codex gate round 6: no alternatives through filler; clock ranges validated --------------------

@pytest.mark.parametrize("phrase", ["at 8 in the morning or in the evening", "at 8 in the morning or evening",
                                    "tomorrow at 8 in the morning or in the evening", "at 3 or so",
                                    "at 25", "at 9:99am", "tomorrow at 13pm", "at 0am"])
def test_alternatives_and_impossible_clocks_stay_unknown(phrase):
    d = due(phrase, anchor="2026-11-24T10:00:00Z", tz="America/New_York")
    assert d.due_ts is None, d


# --- review round 7: a bare clock follows a part-of-day word anywhere in the phrase ---------------

@pytest.mark.parametrize("phrase,expected", [
    ("tomorrow evening at eight o'clock", "2026-11-25T20:00:00-05:00"),
    ("tomorrow night at nine", "2026-11-25T21:00:00-05:00"),
    ("tomorrow morning at five o'clock", "2026-11-25T05:00:00-05:00"),
    ("tomorrow afternoon at two", "2026-11-25T14:00:00-05:00"),
])
def test_a_bare_clock_follows_the_part_of_day_word(phrase, expected):
    assert due(phrase).due_ts == expected
