"""ICS timestamps built at test time: compact UTC stamps are never written as literals in source
(the repo's operator-identifier scan reads them as incident ids)."""
import datetime as dt
import re


def ics_ts(when):
    """A UTC datetime as an ICS UTC stamp (date, 'T', time, 'Z')."""
    if when.tzinfo is not None:
        when = when.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return f"{when:%Y%m%dT%H%M%S}Z"


def Z(*parts):
    """ics_ts of dt.datetime(*parts) taken as UTC: Z(2026, 11, 24, 19) is 19:00 UTC that day."""
    return ics_ts(dt.datetime(*parts))


_PLACEHOLDER = re.compile(r"\{Z:(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})\}")


def fill(template):
    """Replace every {Z:YYYY-MM-DD HH:MM} placeholder (UTC) with its ICS stamp."""
    return _PLACEHOLDER.sub(lambda m: Z(*map(int, m.groups())), template)
