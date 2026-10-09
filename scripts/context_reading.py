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

The transcript's last CONTEXT-CHANGING entry is used (user / assistant / attachment / a
compaction): not the file's mtime (transcripts are touched without being appended to; one had
today's mtime and a last entry two days old), and not housekeeping lines, which carry timestamps
too. The rule lives in lineage_daemon/wal/ctx_adapters.py, shared with the rotation engine.

KNOWN WINDOW: STATUSLINE_SLACK_S admits a reading taken up to that long before the last entry, so
for one redraw cycle of_window can read slightly LOW (a big file read in that minute is not in it
yet). It corrects on the next redraw. Nulling those readings instead would blank seats mid-turn.
"""
import json
import os
import sys

# ONE definition of "fresh", shared with the rotation engine (lineage_daemon/wal/ctx_adapters.py):
# which transcript entries change the context, how far back to read, and the status line's lag.
_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from lineage_daemon.wal.ctx_adapters import TRANSCRIPT_SLACK_S as STATUSLINE_SLACK_S  # noqa: E402
from lineage_daemon.wal.ctx_adapters import ctx_bridge_path, last_ctx_entry_epoch  # noqa: E402


def bridge_path(session_id: str, tmp_dir: str | None = None) -> str:
    """Where the status line writes the reading (ctx_adapters.ctx_bridge_path: $TMPDIR or /tmp)."""
    return ctx_bridge_path(session_id, tmp_dir)


def last_entry_ts(transcript_path: str | None) -> float | None:
    """Epoch seconds of the newest CONTEXT-CHANGING transcript entry, or None (ctx_adapters)."""
    return last_ctx_entry_epoch(transcript_path) if transcript_path else None


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
