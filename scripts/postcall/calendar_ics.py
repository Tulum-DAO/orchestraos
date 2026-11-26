"""Calendar ICS reader for post-call commitments: design §2a/§3 (docs/DESIGN_post-call-commitments.md).

Two questions are asked of Shaw's calendar feed (~/.orchestra/state/calendar/shaw.ics):

  * §2a — which event does a recorded call belong to? Its attendee list tells us who was invited,
    which is how speaker labels and counterparts become verified (by email, never by name).
  * §3  — when is the next meeting with a given attendee? That resolves "by the next call" deadlines.

This is a deliberately small RFC 5545 reader. The design names `icalendar` + `recurring_ical_events`,
but this box keeps the post-call suite stdlib-only, so it implements exactly the subset that a
Google/Outlook export of a personal calendar uses:

  * content lines: unfolding (CRLF or LF followed by a space or tab), property parameters with
    quoted values, TEXT unescaping;
  * VEVENT only (VALARM and other nested components are skipped; VTIMEZONE is ignored because the
    IANA TZID is resolved with zoneinfo instead);
  * DTSTART/DTEND as UTC (`...Z`), TZID-qualified local time, floating time (read as UTC) or DATE
    (all-day, 00:00 UTC); DURATION when DTEND is missing;
  * RRULE with FREQ=DAILY|WEEKLY|MONTHLY, INTERVAL, COUNT, UNTIL, BYDAY, BYMONTHDAY; EXDATE;
    RECURRENCE-ID overrides; STATUS:CANCELLED.

Recurrences are stepped in the DTSTART's own wall-clock time and only then converted to UTC, so a
weekly 10:00 New York meeting stays at 10:00 New York across a DST change (its UTC hour moves).

Bad input never raises: an event that cannot be understood is skipped and the rest are returned. A
missing feed is an empty list, and callers treat that as "unknown", never as "no meeting".
"""
import calendar as _calendar
import datetime as dt
import re
from dataclasses import dataclass, field, replace
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc

_WEEKDAY_CODES = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_DURATION = re.compile(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")
_DATE_TIME = re.compile(r"(\d{8}T\d{6})(Z?)")
_DATE = re.compile(r"\d{8}")
# RRULE parts this reader understands. A rule using anything else (BYSETPOS, BYMONTH, BYHOUR, ...)
# is not guessed at: only its first occurrence is returned (see _occurrences).
_SUPPORTED_RULE_PARTS = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "WKST"}
# Attendee calendar-user types that are not people (meeting rooms, projectors). They must not count
# as invitees, or the §2a two-party shortcut ("exactly one non-Shaw attendee") breaks on every
# booked room.
_NON_PERSON_CUTYPES = {"RESOURCE", "ROOM"}


@dataclass(frozen=True)
class Attendee:
    email: str                      # lowercased, without "mailto:"
    cn: str = ""                    # display name as given (quotes removed)
    partstat: str = "NEEDS-ACTION"  # uppercased; RFC 5545 default when absent


@dataclass(frozen=True)
class Event:
    uid: str
    recurrence_id: str | None       # UTC "YYYYMMDDTHHMMSSZ" of the original start; None on masters
    start: dt.datetime              # timezone-aware, UTC
    end: dt.datetime                # timezone-aware, UTC
    summary: str
    status: str                     # uppercased STATUS, "" when absent
    attendees: tuple[Attendee, ...]
    all_day: bool = False
    # Recurrence data, kept off the public surface and out of equality/hash. Only masters carry a
    # rule; expanded instances and overrides have these empty.
    _tzid: str | None = field(default=None, repr=False, compare=False)
    _rrule: tuple[tuple[str, str], ...] = field(default=(), repr=False, compare=False)
    _exdates: frozenset[str] = field(default=frozenset(), repr=False, compare=False)


# --- parsing ------------------------------------------------------------------------------------

def parse(text: str) -> list[Event]:
    """Parse VEVENTs as given: masters (with their RRULE/EXDATE stored, not expanded) and
    RECURRENCE-ID overrides. Malformed events are skipped; this never raises."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if not isinstance(text, str):
        return []
    events: list[Event] = []
    current: list | None = None     # properties of the VEVENT being read, or None outside one
    depth = 0                       # nesting inside the VEVENT (VALARM etc.), whose lines we skip
    for line in _unfold(text):
        prop = _split_property(line)
        if prop is None:
            continue
        name, params, value = prop
        kind = value.strip().upper()
        if name == "BEGIN":
            if kind == "VEVENT" and depth == 0:
                current = []        # also abandons an unterminated VEVENT before this one
            elif current is not None:
                depth += 1
            continue
        if name == "END":
            if current is not None:
                if depth:
                    depth -= 1
                elif kind == "VEVENT":
                    event = _build_event(current)
                    if event is not None:
                        events.append(event)
                    current = None
            continue
        if current is not None and depth == 0:
            current.append((name, params, value))
    return events


def load(path) -> list[Event]:
    """Parse the ICS file at `path`; [] if it is missing or unreadable."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return parse(f.read())
    except (OSError, TypeError, ValueError):
        return []


def _unfold(text):
    # RFC 5545 §3.1: a line break followed by a single space or tab is a continuation.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n[ \t]", "", text).split("\n")


def _split_property(line):
    """`NAME;P1=v;P2="q:v":value` -> (NAME, {P1: v, P2: q:v}, value), or None if no colon.
    Colons and semicolons inside double-quoted parameter values do not split."""
    in_quotes = False
    head_parts, start = [], 0
    for i, ch in enumerate(line):
        if ch == '"':
            in_quotes = not in_quotes
        elif not in_quotes and ch == ";":
            head_parts.append(line[start:i])
            start = i + 1
        elif not in_quotes and ch == ":":
            head_parts.append(line[start:i])
            name = head_parts[0].strip().upper()
            if not name:
                return None
            params = {}
            for part in head_parts[1:]:
                key, sep, val = part.partition("=")
                if sep:
                    params[key.strip().upper()] = val.strip().strip('"')
            return name, params, line[i + 1:]
    return None


def _unescape(text):
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), text)


def _build_event(props):
    # One unparseable event must never take the whole feed down (design §3: a feed we cannot read
    # means "deadline unknown", not a crash), so anything that goes wrong here skips this event.
    try:
        return _build_event_strict(props)
    except Exception:
        return None


def _build_event_strict(props):
    single = {}                     # first occurrence of each single-valued property
    attendees, exdates = [], set()
    for name, params, value in props:
        if name == "ATTENDEE":
            attendee = _attendee(params, value)
            if attendee is not None:
                attendees.append(attendee)
        elif name == "EXDATE":
            for part in value.split(","):
                try:
                    exdates.add(_key(_parse_when(part, params)[0]))
                except Exception:
                    continue        # one bad EXDATE value should not lose the whole series
        else:
            single.setdefault(name, (params, value))

    uid = single.get("UID", ({}, ""))[1].strip()
    if not uid or "DTSTART" not in single:
        return None
    start, tzid, all_day = _parse_when(single["DTSTART"][1], single["DTSTART"][0])

    if "DTEND" in single:
        end = _parse_when(single["DTEND"][1], single["DTEND"][0])[0]
    elif "DURATION" in single:
        end = start + _parse_duration(single["DURATION"][1])
    elif all_day:
        end = start + dt.timedelta(days=1)   # RFC 5545 §3.6.1: a DATE DTSTART alone spans one day
    else:
        end = start + dt.timedelta(hours=1)
    if end < start:
        return None

    recurrence_id = None
    if "RECURRENCE-ID" in single:
        recurrence_id = _key(_parse_when(single["RECURRENCE-ID"][1], single["RECURRENCE-ID"][0])[0])

    rrule = ()
    if "RRULE" in single and recurrence_id is None:
        parts = {}
        for part in single["RRULE"][1].split(";"):
            key, sep, val = part.partition("=")
            if sep:
                parts[key.strip().upper()] = val.strip().upper()
        rrule = tuple(sorted(parts.items()))

    return Event(
        uid=uid,
        recurrence_id=recurrence_id,
        start=start,
        end=end,
        summary=_unescape(single.get("SUMMARY", ({}, ""))[1]).strip(),
        status=single.get("STATUS", ({}, ""))[1].strip().upper(),
        attendees=tuple(attendees),
        all_day=all_day,
        _tzid=tzid,
        _rrule=rrule,
        _exdates=frozenset(exdates),
    )


def _attendee(params, value):
    if params.get("CUTYPE", "").upper() in _NON_PERSON_CUTYPES:
        return None
    email = value.strip()
    if email.lower().startswith("mailto:"):
        email = email[len("mailto:"):]
    email = email.strip().lower()
    if not email:
        return None
    return Attendee(email=email, cn=params.get("CN", "").strip(),
                    partstat=(params.get("PARTSTAT") or "NEEDS-ACTION").strip().upper())


def _parse_when(value, params):
    """A DATE or DATE-TIME value -> (UTC datetime, TZID used or None, is_all_day).

    `...Z` is UTC (and wins over a stray TZID); TZID is resolved with zoneinfo (an unknown TZID
    raises, so the event is skipped rather than silently mis-timed by hours); floating local time
    has no zone to anchor it, so it is read as UTC; a DATE is 00:00 UTC that day."""
    value = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or _DATE.fullmatch(value):
        day = dt.datetime.strptime(value, "%Y%m%d")
        return day.replace(tzinfo=UTC), None, True
    match = _DATE_TIME.fullmatch(value)
    if not match:
        raise ValueError(f"not an ICS date-time: {value!r}")
    naive = dt.datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")
    if match.group(2):
        return naive.replace(tzinfo=UTC), None, False
    tzid = params.get("TZID", "").strip()
    if tzid:
        return naive.replace(tzinfo=ZoneInfo(tzid)).astimezone(UTC), tzid, False
    return naive.replace(tzinfo=UTC), None, False


def _parse_duration(value):
    match = _DURATION.fullmatch(value.strip())
    if not match or not any(match.groups()[1:]):
        raise ValueError(f"not an ICS duration: {value!r}")
    sign, weeks, days, hours, minutes, seconds = match.groups()
    delta = dt.timedelta(weeks=int(weeks or 0), days=int(days or 0), hours=int(hours or 0),
                         minutes=int(minutes or 0), seconds=int(seconds or 0))
    return -delta if sign == "-" else delta


def _key(when):
    """Canonical instance identity: the original start in UTC, as RECURRENCE-ID would write it."""
    return when.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _aware(when):
    """Caller-supplied datetimes: naive ones are taken as UTC, aware ones converted to UTC."""
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)


# --- expansion ----------------------------------------------------------------------------------

def expand(events, window_start, window_end) -> list[Event]:
    """Concrete, non-cancelled instances overlapping [window_start, window_end), sorted by start.

    Masters with an RRULE are expanded; EXDATEs remove instances; an override (same UID,
    RECURRENCE-ID equal to an instance's original start) replaces that instance wherever it moved
    to. Never raises: a master whose rule cannot be read contributes only its first occurrence."""
    lo, hi = _aware(window_start), _aware(window_end)
    masters = {e.uid: e for e in events if e.recurrence_id is None}
    overrides = {(e.uid, e.recurrence_id): e for e in events if e.recurrence_id is not None}

    out = []
    for master in masters.values():
        if master.status == "CANCELLED":
            continue
        if not master._rrule:
            out.append(master)
            continue
        try:
            out.extend(_instances(master, hi, overrides))
        except Exception:
            out.append(master)

    for (uid, rid), override in overrides.items():
        master = masters.get(uid)
        if override.status == "CANCELLED":
            continue                # a cancelled instance: its slot is already suppressed
        if master is not None and (master.status == "CANCELLED" or rid in master._exdates):
            continue                # the whole series is cancelled, or this instance was deleted
        out.append(override)        # also covers orphan overrides (invited to one instance only)

    seen, result = set(), []
    for event in sorted(out, key=lambda e: (e.start, e.uid)):
        identity = (event.uid, event.start)   # a feed can list the same event twice
        if event.start < hi and event.end > lo and identity not in seen:
            seen.add(identity)
            result.append(event)
    return result


def _instances(master, hi, overrides):
    """Instances of one recurring master starting before `hi`, minus EXDATEs and overridden slots."""
    rule = dict(master._rrule)
    tz = ZoneInfo(master._tzid) if master._tzid else UTC
    duration = master.end - master.start
    count = int(rule["COUNT"]) if "COUNT" in rule else None
    until = _parse_until(rule["UNTIL"], tz) if "UNTIL" in rule else None
    # Wall-clock start in the event's own zone: recurrence steps happen here, not in UTC.
    local_start = master.start.astimezone(tz).replace(tzinfo=None)
    local_stop = hi.astimezone(tz).replace(tzinfo=None) + dt.timedelta(days=1)

    generated = 0
    for local in _occurrences(local_start, rule, local_stop):
        start = local.replace(tzinfo=tz).astimezone(UTC)
        if (count is not None and generated >= count) or (until is not None and start > until):
            break
        if start >= hi:
            break
        generated += 1              # EXDATE'd and overridden instances still count toward COUNT
        key = _key(start)
        if key in master._exdates or (master.uid, key) in overrides:
            continue
        yield replace(master, recurrence_id=key, start=start, end=start + duration,
                      _rrule=(), _exdates=frozenset())


def _parse_until(value, tz):
    """UNTIL is inclusive. A DATE means through the end of that local day; a floating value is
    local to the event's zone; a `...Z` value is UTC."""
    value = value.strip()
    if _DATE.fullmatch(value):
        local_end = dt.datetime.strptime(value, "%Y%m%d") + dt.timedelta(days=1, microseconds=-1)
        return local_end.replace(tzinfo=tz).astimezone(UTC)
    match = _DATE_TIME.fullmatch(value)
    if not match:
        raise ValueError(f"bad UNTIL: {value!r}")
    naive = dt.datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")
    return naive.replace(tzinfo=UTC if match.group(2) else tz).astimezone(UTC)


def _occurrences(local_start, rule, local_stop):
    """Yield naive local start times in order, beginning with DTSTART itself, until the period
    being generated passes `local_stop` (that bound also ends rules that can never match, such as
    BYMONTHDAY=31 in a FEB-only world, instead of looping forever)."""
    freq = rule.get("FREQ", "")
    interval = int(rule.get("INTERVAL", "1"))
    if interval < 1:
        raise ValueError("INTERVAL must be >= 1")
    yield local_start               # DTSTART is always the first instance
    if freq not in ("DAILY", "WEEKLY", "MONTHLY") or set(rule) - _SUPPORTED_RULE_PARTS:
        return                      # unsupported rule: first occurrence only, never a wrong guess
    byday = [d.strip() for d in rule.get("BYDAY", "").split(",") if d.strip()]

    if freq == "DAILY":
        step = dt.timedelta(days=interval)
        current = local_start + step
        while current <= local_stop:
            yield current
            current += step

    elif freq == "WEEKLY":
        # Weeks begin on WKST (default Monday). It only matters with INTERVAL > 1, where it decides
        # which days share a week, but Google exports WKST=SU, so it is honoured.
        wkst = _weekday(rule.get("WKST", "MO"))
        offsets = sorted({(_weekday(code) - wkst) % 7 for code in byday}) or [(local_start.weekday() - wkst) % 7]
        week = local_start - dt.timedelta(days=(local_start.weekday() - wkst) % 7)
        while week <= local_stop:
            for offset in offsets:
                candidate = week + dt.timedelta(days=offset)
                if candidate > local_start:
                    yield candidate
            week += dt.timedelta(weeks=interval)

    else:  # MONTHLY
        monthdays = [int(d) for d in rule.get("BYMONTHDAY", "").split(",") if d.strip()]
        if any(d == 0 or not -31 <= d <= 31 for d in monthdays):
            raise ValueError("BYMONTHDAY out of range")
        year, month = local_start.year, local_start.month
        while dt.datetime(year, month, 1) <= local_stop:
            days = _month_days(year, month, monthdays, byday, local_start.day)
            for day in days:
                candidate = local_start.replace(year=year, month=month, day=day)
                if candidate > local_start:
                    yield candidate
            month += interval
            year, month = year + (month - 1) // 12, (month - 1) % 12 + 1


def _weekday(code):
    """'MO' -> 0. A numeric prefix ('2TU', '-1FR') is returned separately by _ordinal_weekday."""
    return _WEEKDAY_CODES[code[-2:].upper()]


def _month_days(year, month, monthdays, byday, default_day):
    """Days of `month` a MONTHLY rule selects, ascending. Months too short for the day are skipped
    (RFC 5545: an invalid date is ignored, not clamped)."""
    last = _calendar.monthrange(year, month)[1]
    by_monthday = set()
    for d in monthdays:
        day = d if d > 0 else last + 1 + d
        if 1 <= day <= last:
            by_monthday.add(day)
    by_weekday = set()
    for code in byday:              # "1MO" = first Monday, "-1FR" = last Friday, "TU" = every Tuesday
        ordinal, weekday = code[:-2], _weekday(code)
        matching = [d for d in range(1, last + 1) if dt.date(year, month, d).weekday() == weekday]
        if not ordinal:
            by_weekday.update(matching)
        else:
            n = int(ordinal)
            if n != 0 and abs(n) <= len(matching):
                by_weekday.add(matching[n - 1] if n > 0 else matching[n])
    if monthdays and byday:
        return sorted(by_monthday & by_weekday)   # both given: a day must satisfy both (RFC 5545)
    if monthdays or byday:
        return sorted(by_monthday | by_weekday)
    return [default_day] if default_day <= last else []


# --- the questions callers ask ------------------------------------------------------------------

def _overlap(event, lo, hi):
    return max(dt.timedelta(0), min(event.end, hi) - max(event.start, lo))


def match_call(events, call_start, call_end, slack=dt.timedelta(minutes=10)) -> Event | None:
    """The calendar event a recording belongs to (design §2a), or None when there is none or the
    answer is ambiguous.

    Candidates overlap the call widened by `slack` on each side. The winner has the largest overlap
    with the call itself; if nothing truly overlaps (a recording started a few minutes after the
    meeting slot ended), the slack-widened overlap decides. A tie for the largest is ambiguous and
    returns None: guessing would attach the wrong invitees to a call. All-day events are never a
    call's event (a holiday spans every call that day and would create spurious ties)."""
    start, end = _aware(call_start), _aware(call_end)
    lo, hi = start - slack, end + slack
    candidates = [e for e in expand(events, lo, hi) if not e.all_day]
    if not candidates:
        return None
    scores = [_overlap(e, start, end) for e in candidates]
    if max(scores) <= dt.timedelta(0):
        scores = [_overlap(e, lo, hi) for e in candidates]
    best = max(scores)
    if best <= dt.timedelta(0):
        return None                 # only zero-length touches: no evidence either way
    winners = [e for e, score in zip(candidates, scores) if score == best]
    return winners[0] if len(winners) == 1 else None


def invited(event, shaw_emails: set[str]) -> list[Attendee]:
    """The event's people other than Shaw, minus anyone who declined."""
    mine = {e.strip().lower() for e in shaw_emails}
    return [a for a in event.attendees if a.email not in mine and a.partstat != "DECLINED"]


def next_with_attendee(events, email, after, horizon=dt.timedelta(days=60)) -> Event | None:
    """The earliest non-cancelled event starting in (after, after + horizon] that `email` is an
    attendee of and has not declined (design §3 "by the next call"). None means unknown."""
    target = email.strip().lower()
    lo = _aware(after)
    hi = lo + horizon
    # expand() treats the window end as exclusive; nudge it so a start exactly at `hi` counts.
    for event in expand(events, lo, hi + dt.timedelta(microseconds=1)):
        if not lo < event.start <= hi:
            continue
        if any(a.email == target and a.partstat != "DECLINED" for a in event.attendees):
            return event
    return None
