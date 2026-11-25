"""Deadlines (design §3). Lifted from commitment_miner.resolve_due_ts (DEC-1789352893701528 rules:
17:00 default, tonight 21:00, before-<weekday> 09:00, in N days) with three changes: the zone is
Shaw's zone at the call (not hardcoded ET), a clock in the phrase is honoured, and a bare
day-of-month is ambiguous rather than guessed. Unknown stays unknown and says why."""
import datetime as dt
import re
from typing import NamedTuple
from zoneinfo import ZoneInfo

_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6}
_MONTHS = {m: n for n, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTH_DAY = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b")
_DAY_ONLY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b|\b(first|second|third|fourth|fifth|tenth|fifteenth|"
                       r"twentieth|thirtieth)\b")
_CLOCK = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|\bat\s+(\d{1,2})(?::(\d{2}))?\b")
_PARTS = (("morning", 9, 0), ("noon", 12, 0), ("afternoon", 15, 0), ("end of day", 17, 0),
          ("eod", 17, 0), ("evening", 21, 0), ("tonight", 21, 0))


class Due(NamedTuple):
    due_ts: str | None
    due_source: str


def zone_at_call(created_at, location):
    """(tz, label, assumed). shaw-location.json keeps only the current zone and `since`; if he moved
    after the call there is no history, so use home base and say the zone is assumed."""
    call_day = _parse_utc(created_at).date().isoformat()
    since = (location.get("since") or "")[:10]
    if since and since <= call_day:
        return location["tz"], location.get("label") or location["tz"], False
    home = location.get("home_base") or {}
    return home.get("tz") or location["tz"], home.get("label") or location.get("label") or "", True


def _parse_utc(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def _next_weekday(day, target):
    delta = (target - day.weekday()) % 7
    return day + dt.timedelta(days=7 if delta == 0 else delta)


def _date_of(p, today):
    """(date, default_hour) or (None, reason)."""
    m = _MONTH_DAY.search(p)
    if m:
        d = dt.date(today.year, _MONTHS[m.group(1)], int(m.group(2)))
        return (d if d >= today else d.replace(year=today.year + 1)), 17
    for name, wd in _WEEKDAYS.items():
        if re.search(rf"\b{name}\b", p):
            return _next_weekday(today, wd), (9 if "before" in p else 17)
    if "tomorrow" in p:
        return today + dt.timedelta(days=1), 17
    if "tonight" in p or "today" in p or "end of day" in p or "eod" in p:
        return today, 17
    if "week or two" in p or "two weeks" in p:
        return today + dt.timedelta(days=14), 17
    if "next week" in p:
        return _next_weekday(today, 0), 17
    if "this week" in p:
        return _next_weekday(today, 4), 17
    m = re.search(r"\bin\s+(\d+)\s+days?\b", p)
    if m:
        return today + dt.timedelta(days=int(m.group(1))), 17
    if _DAY_ONLY.search(p):
        return None, "ambiguous"
    return None, "unknown"


def _clock_of(p, default_hour):
    m = _CLOCK.search(p)
    if m:
        hour = int(m.group(1) or m.group(4))
        minute = int(m.group(2) or m.group(5) or 0)
        ampm = m.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        elif not ampm and 1 <= hour <= 7:
            hour += 12  # "at 3" in a business conversation means the afternoon
        return hour, minute
    for word, h, mi in _PARTS:
        if word in p:
            return h, mi
    return default_hour, 0


def _localize(day, hour, minute, tz):
    """Aware datetime for a wall time, plus whether DST forced a choice (gap -> first valid minute
    after; overlap -> first occurrence, fold=0)."""
    zone = ZoneInfo(tz)
    wall = dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
    if wall.replace(fold=1).utcoffset() != wall.utcoffset():
        exists = wall.astimezone(dt.timezone.utc).astimezone(zone).replace(tzinfo=None) == wall.replace(tzinfo=None)
        if exists:
            return wall, True  # overlap: fold=0 is the first occurrence
        probe = wall.replace(tzinfo=None)
        while True:
            probe += dt.timedelta(minutes=1)
            cand = probe.replace(tzinfo=zone)
            if cand.astimezone(dt.timezone.utc).astimezone(zone).replace(tzinfo=None) == probe:
                return cand, True
    return wall, False


def resolve(timeframe, anchor, tz, *, zone_assumed=None):
    phrase = (timeframe or "").strip()
    if not phrase:
        return Due(None, "none stated")
    p = phrase.lower()
    today = _parse_utc(anchor).astimezone(ZoneInfo(tz)).date()
    day, default_hour = _date_of(p, today)
    if day is None:
        return Due(None, f"{default_hour}: {phrase}" if default_hour == "ambiguous" else f"unknown ({phrase})")
    hour, minute = (17, 0) if zone_assumed else _clock_of(p, default_hour)
    when, dst_adjusted = _localize(day, hour, minute, tz)
    source = phrase
    if zone_assumed:
        source += f" (zone assumed: {zone_assumed})"
    if dst_adjusted:
        source += " (DST-adjusted)"
    return Due(when.isoformat(timespec="seconds"), source)


def _add_business_days(day, n):
    while n:
        day += dt.timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day


def chase_date(their_due_ts, call_start, tz):
    """When Shaw chases a participant's promise (design §4): their due + 1 business day at 10:00 when
    known, else the call date + 2 business days at 10:00, in the zone at the call."""
    zone = ZoneInfo(tz)
    if their_due_ts:
        base, n = dt.datetime.fromisoformat(their_due_ts).astimezone(zone).date(), 1
    else:
        base, n = _parse_utc(call_start).astimezone(zone).date(), 2
    when, _ = _localize(_add_business_days(base, n), 10, 0, tz)
    return when.isoformat(timespec="seconds")
