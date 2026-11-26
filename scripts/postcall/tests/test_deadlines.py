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
