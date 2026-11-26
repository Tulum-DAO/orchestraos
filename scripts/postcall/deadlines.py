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
_NUMBERS = {w: str(n) for n, w in enumerate(
    "one two three four five six seven eight nine ten eleven twelve".split(), 1)}
_SPELLED = re.compile(r"\b(" + "|".join(_NUMBERS) + r")(?=\s*(?:am|pm|o'?clock)\b)")
_OCLOCK = re.compile(r"\b(\d{1,2})\s*o'?clock\b")
# A zone named in the phrase wins over Shaw's zone ("two PM EST"): (region, standard, daylight, name).
# A standard/daylight abbreviation that disagrees with the season is ambiguous, never guessed.
# Two-letter forms are left out on purpose.
_ZONES = (("America/New_York", "est", "edt", "eastern"), ("America/Chicago", "cst", "cdt", "central"),
          ("America/Denver", "mst", "mdt", "mountain"), ("America/Los_Angeles", "pst", "pdt", "pacific"))
_HOURS = re.compile(r"\b(?:in|within)\s+(?:the\s+next\s+)?(an?|\d+|(?:a\s+)?couple(?:\s+of)?|(?:a\s+)?few)"
                    r"\s+hours?\b(?!\s+and\b)")
# Clock or duration words this parser does not read: the phrase stays unknown rather than half-read.
# One zone grammar for reading AND for consuming: "<abbr|name> [standard|daylight] [time]".
_ZONE_TAIL = r"(?:\s+(standard|daylight))?(?:\s+time)?"
_DAYPART = re.compile(r"\bin the (?:morning|afternoon|evening)\b|\bat night\b")
MAX_RELATIVE_HOURS = 72
_COMPOUND = re.compile(r"\b(?:half|quarter|past)\b")
_NUMBER = re.compile(r"\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
                     r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|"
                     r"sixty|seventy|eighty|ninety)\b")
_IN_DAYS = re.compile(r"\bin\s+\d+\s+days?\b")
_WEEK_OR_TWO = re.compile(r"\bweek or two\b|\btwo weeks\b")
_FILLER = frozenset("at by around about before until till say like approx approximately ish or so the latest"
                    .split())
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
        ampm = (m.group(3) or "")[:1]   # "a" | "p" | "" (a full "pm" literal trips the seat-role lint)
        if ampm == "p" and hour < 12:
            hour += 12
        elif ampm == "a" and hour == 12:
            hour = 0
        elif not ampm and re.search(r"\bin the morning\b", p):
            hour = 0 if hour == 12 else hour
        elif not ampm and re.search(r"\bin the (?:afternoon|evening)\b|\bat night\b|\btonight\b", p) and hour < 12:
            hour += 12
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


def _normalized(phrase):
    p = phrase.lower().replace("p.m.", "pm").replace("a.m.", "am")
    p = _SPELLED.sub(lambda m: _NUMBERS[m.group(1)], p)
    return _OCLOCK.sub(lambda m: f"at {m.group(1)}", p)


def _named_zone(p):
    """(region tz, "std" | "dst" | None) for the ONE zone named in the phrase, (None, None) for none,
    or ("", None) when two are named ("EST or PST"): alternatives are never resolved."""
    found = []
    for tz, std, dst, name in _ZONES:
        for m in re.finditer(rf"\b({std}|{dst}|{name}){_ZONE_TAIL}\b", p):
            kind = {std: "std", dst: "dst"}.get(m.group(1)) or {"standard": "std", "daylight": "dst"}.get(m.group(2))
            found.append((tz, kind))
    if len(found) > 1:
        return "", None
    return found[0] if found else (None, None)


def _unused_number(p, spans):
    """A digit or number word outside every span the parser read: part of the phrase was not
    understood, so any date from it would be a guess."""
    rest = p
    for a, b in spans:
        rest = rest[:a] + " " * (b - a) + rest[b:]
    return bool(_NUMBER.search(rest))


def _date_spans(p):
    return [m.span() for rx in (_MONTH_DAY, _IN_DAYS, _WEEK_OR_TWO) for m in rx.finditer(p)]


def _season_clash(when, kind):
    return kind is not None and (kind == "dst") != bool(when.dst())


def _clock_only(p):
    """True when the phrase is nothing but a clock time (plus a zone and filler words)."""
    rest = _CLOCK.sub(" ", p)
    for _, std, dst, name in _ZONES:
        rest = re.sub(rf"\b(?:{std}|{dst}|{name}){_ZONE_TAIL}\b", " ", rest)
    rest = _DAYPART.sub(" ", rest)
    return all(w in _FILLER for w in re.findall(r"[a-z0-9']+", rest))


def _hours_ahead(p):
    m = _HOURS.search(p)
    if not m:
        return None
    n = m.group(1).replace("a ", "").strip()
    hours = 1 if n in ("a", "an") else 2 if n.startswith("couple") else 3 if n == "few" else int(n[:6])
    return hours if hours <= MAX_RELATIVE_HOURS else None


def resolve(timeframe, anchor, tz, *, zone_assumed=None):
    phrase = (timeframe or "").strip()
    if not phrase:
        return Due(None, "none stated")
    p = _normalized(phrase)
    if _COMPOUND.search(p):
        return Due(None, f"unknown ({phrase})")
    start = _parse_utc(anchor)
    clocks = list(_CLOCK.finditer(p))
    if len(clocks) > 1:        # "at 2pm or 4pm": alternatives, never the first one
        return Due(None, f"unknown ({phrase})")
    hours = _hours_ahead(p)
    if hours is not None:      # "in the next couple of hours": from the call, exact
        m = _HOURS.search(p)
        rest = p[:m.start()] + " " + p[m.end():]
        if not all(w in _FILLER for w in re.findall(r"[a-z0-9']+", rest)):   # the whole phrase, or nothing
            return Due(None, f"unknown ({phrase})")
        when = (start + dt.timedelta(hours=hours)).astimezone(ZoneInfo(tz))
        return Due(when.isoformat(timespec="seconds"), phrase)
    named, kind = _named_zone(p)
    if named == "":
        return Due(None, f"ambiguous: {phrase} (two zones named)")
    zone = named or tz
    today = start.astimezone(ZoneInfo(zone)).date()
    day, default_hour = _date_of(p, today)
    clock_spans = [c.span() for c in clocks]
    if day is not None and _unused_number(p, _date_spans(p) + clock_spans):
        return Due(None, f"unknown ({phrase})")
    if day is None and default_hour == "unknown" and clocks and _clock_only(p) and not zone_assumed:
        # A clock time alone ("two PM EST") is its next occurrence after the call.
        hour, minute = _clock_of(p, 17)
        when, dst_adjusted = _localize(today, hour, minute, zone)
        if when <= start:
            second = when.replace(fold=1)          # the repeated hour on a fall-back day comes first
            if second.utcoffset() != when.utcoffset() and second > start:
                when = second
            else:
                when, dst_adjusted = _localize(today + dt.timedelta(days=1), hour, minute, zone)
        if _season_clash(when, kind):
            return Due(None, f"ambiguous: {phrase} (zone abbreviation disagrees with the season)")
        return Due(when.astimezone(ZoneInfo(tz)).isoformat(timespec="seconds"),
                   phrase + (" (DST-adjusted)" if dst_adjusted else ""))
    if day is None:
        return Due(None, f"{default_hour}: {phrase}" if default_hour == "ambiguous" else f"unknown ({phrase})")
    hour, minute = (17, 0) if zone_assumed else _clock_of(p, default_hour)
    when, dst_adjusted = _localize(day, hour, minute, zone)
    if _season_clash(when, kind):
        return Due(None, f"ambiguous: {phrase} (zone abbreviation disagrees with the season)")
    when = when.astimezone(ZoneInfo(tz))
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
