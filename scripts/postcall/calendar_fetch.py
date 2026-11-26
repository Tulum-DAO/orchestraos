"""Shaw's calendar feed (design §3): the secret iCal URL is read BY REFERENCE from a 0600 file and
fetched read-only into the local copy calendar_ics.load() reads. The URL is a secret: it is never
printed, logged or put in a status; errors name only the exception type.

    refresh() -> "no-url" | "fresh" | "fetched" | "error: <why, without the URL>"

The network call is injected (`opener`) so tests never reach it."""
import os
import tempfile
import time
import urllib.request

MAX_AGE_S = 1800            # re-fetch at most every 30 min (the sweep runs every 5)
_MAX_BYTES = 10_000_000


def url_file():
    return os.environ.get("POSTCALL_CALENDAR_URL_FILE") or os.path.expanduser("~/.secrets/shaw-calendar.ics.url")


def calendar_file():
    return os.environ.get("POSTCALL_CALENDAR_FILE") or os.path.expanduser("~/.orchestra/state/calendar/shaw.ics")


def _urlopen(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "postcall-calendar/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(_MAX_BYTES + 1).decode("utf-8", "replace")


def _complete(body):
    """A whole calendar: one VCALENDAR root as the first and last (unfolded) lines, components
    balanced, so a truncated download never replaces a good copy."""
    unfolded = []
    for ln in body.splitlines():
        if ln[:1] in (" ", "\t") and unfolded:      # RFC 5545 folding: a continuation of the line before
            unfolded[-1] += ln[1:]
        elif ln.strip():
            unfolded.append(ln.strip())
    lines = unfolded
    if (len(lines) < 2 or lines[0] != "BEGIN:VCALENDAR" or lines[-1] != "END:VCALENDAR"
            or lines.count("BEGIN:VCALENDAR") != 1):
        return False
    depth = []
    for ln in lines:
        if ln.startswith("BEGIN:"):
            depth.append(ln[6:])
        elif ln.startswith("END:"):
            if not depth or depth.pop() != ln[4:]:
                return False
    return not depth


def refresh(url_path=None, out_path=None, *, opener=None, now=None, max_age_s=MAX_AGE_S, timeout=20):
    url_path, out_path = url_path or url_file(), out_path or calendar_file()
    try:
        with open(url_path) as f:
            url = f.read().strip()
    except FileNotFoundError:
        return "no-url"
    except OSError as e:
        return f"error: url file unreadable ({type(e).__name__})"
    if not url:
        return "no-url"
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if not url.startswith("https://"):
        return "error: the calendar URL is not https"
    now = time.time() if now is None else now
    try:
        if now - os.stat(out_path).st_mtime < max_age_s:
            return "fresh"
    except FileNotFoundError:
        pass
    try:
        body = (opener or _urlopen)(url, timeout)
    except Exception as e:      # never repr(e): it can carry the URL
        return f"error: fetch failed ({type(e).__name__})"
    if not isinstance(body, str) or len(body) > _MAX_BYTES or not _complete(body):
        return "error: the feed is not a complete iCalendar file (old copy kept)"
    d = os.path.dirname(out_path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".shaw.ics.")
    try:
        with os.fdopen(fd, "w", newline="") as f:     # the feed's own CRLF line ends, byte for byte
            f.write(body)
        os.chmod(tmp, 0o600)
        os.replace(tmp, out_path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return "fetched"
