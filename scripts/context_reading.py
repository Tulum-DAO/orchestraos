"""A Claude seat's context, as two numbers that say what they are divided by.

`context_pct` (agent-status) is whatever the seat's status bar draws. The fleet's status line
draws usage SCALED to an 80% budget, so a seat at 39% of its window drew 49%, and nothing on any
surface said which was which (operator report, 2026-10-09). The apps now get both, named by
their denominator:

  context_pct_of_window  share of the model's whole context window in use (what the app shows)
  context_pct_of_budget  the same reading over the rotation budget, i.e. what rotation acts on

ONE SOURCE. Both come from one reading: the status line's bridge file
`<tmpdir>/claude-ctx-<session_id>.json` ({"remaining_percentage", "used_pct", "timestamp"}),
the same file the lineage daemon's rotation engine reads first
(lineage_daemon/wal/ctx_adapters.read_ctx_detectorfile). `remaining_percentage` is Claude Code's
own figure over the real window, so no window size is assumed here; `used_pct` is the budget
figure. They are taken from the same write, so they can never disagree.

FRESH means "taken at or after the seat's last transcript entry". The status line rewrites the
file only when Claude Code redraws it, which happens on activity, so an idle seat's file can be
hours old and still true: its context has not moved. A wall-clock TTL would have blanked 29 of
37 live seats (measured 2026-10-09). A reading older than the transcript's last entry (beyond
STATUSLINE_SLACK_S) is stale, and stale or unprovable is None. Never a remembered value: a
number that looks current and is not is what made the operator stop a healthy seat.

The transcript's last ACTIVITY entry is used: not the file's mtime (transcripts are touched without
being appended to; one had today's mtime and a last entry two days old), and not housekeeping
lines, which carry timestamps too (see _is_activity).

KNOWN WINDOW: STATUSLINE_SLACK_S admits a reading taken up to that long before the last entry, so
for one redraw cycle of_window can read slightly LOW (a big file read in that minute is not in it
yet). It corrects on the next redraw. Nulling those readings instead would blank seats mid-turn.
"""
import json
import os
from datetime import datetime

# The status line may lag the transcript by a redraw plus its own output cache (the fleet's caches
# for 30 s). A reading this much older than the last entry still counts.
STATUSLINE_SLACK_S = 60
_TAIL_BYTES = 262144


def bridge_path(session_id: str, tmp_dir: str | None = None) -> str:
    """Where the status line writes the reading. Node's os.tmpdir() is $TMPDIR or /tmp."""
    return os.path.join(tmp_dir or os.environ.get("TMPDIR") or "/tmp", f"claude-ctx-{session_id}.json")


# What counts as the seat DOING something. Same rule as the rotation engine's freshness check
# (orchestra-builder, 2026-10-09). Housekeeping lines carry timestamps too ("Remote Control
# disconnected", stop-hook summaries, queue operations) and would make an idle seat's true
# reading look stale. A compaction after the reading DOES make it stale.
_ACTIVITY_TYPES = ("user", "assistant", "attachment")
_ACTIVITY_SYSTEM_SUBTYPES = ("compact_boundary",)
# One tool result can be larger than the tail: read further back before giving up.
_TAIL_STEPS = (_TAIL_BYTES, 4 * 1024 * 1024)


def _is_activity(entry: dict) -> bool:
    t = entry.get("type")
    return t in _ACTIVITY_TYPES or (t == "system" and entry.get("subtype") in _ACTIVITY_SYSTEM_SUBTYPES)


def last_entry_ts(transcript_path: str | None) -> float | None:
    """Epoch seconds of the newest ACTIVITY entry (see _is_activity) in the transcript, or None."""
    if not transcript_path:
        return None
    for tail in _TAIL_STEPS:
        try:
            with open(transcript_path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - tail))
                chunk = fh.read().decode("utf-8", "replace")
        except OSError:
            return None
        lines = chunk.split("\n")
        if size > tail:
            lines = lines[1:]             # the first line may be a partial record
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)  # a half-written last line fails here and is skipped
                if not isinstance(entry, dict) or not _is_activity(entry):
                    continue
                return datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).timestamp()
            except (ValueError, AttributeError, TypeError, KeyError):
                continue
        if size <= tail:
            return None                   # read the whole file: there is no activity entry
    return None


def _pct(v) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not 0 <= v <= 100:
        return None
    return int(round(v))


def claude_context(session_id: str | None, transcript_path: str | None, *,
                   tmp_dir: str | None = None) -> tuple[int | None, int | None]:
    """(context_pct_of_window, context_pct_of_budget) for a Claude seat, each an int 0..100 or
    None. Both None unless the reading is present, well-formed and fresh (see module doc)."""
    if not session_id:
        return None, None
    try:
        with open(bridge_path(session_id, tmp_dir)) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None, None
    if not isinstance(d, dict) or d.get("session_id") not in (None, session_id):
        return None, None
    taken = d.get("timestamp")
    entry = last_entry_ts(transcript_path)
    if not isinstance(taken, (int, float)) or entry is None:
        return None, None                 # freshness cannot be shown: not fresh
    if taken < entry - STATUSLINE_SLACK_S:
        return None, None                 # the seat has moved on since this reading
    remaining = _pct(d.get("remaining_percentage"))
    return (None if remaining is None else 100 - remaining), _pct(d.get("used_pct"))
