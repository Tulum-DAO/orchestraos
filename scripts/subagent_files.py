"""Claude subagent transcript files: <session dir>/subagents/agent-<id>.jsonl (+ .meta.json).

Shared by watch_gateway.py (/agents subagent count) and agent_tree.py (the per-node tree), so the
running/finished rule has one implementation.
"""
import json
import os

SUBAGENT_STALE_S = 15 * 60
# Growing tails: a subagent's final answer is often one record longer than 8 KB (median ~12 KB,
# the last whole record starting up to ~43 KB from the end).
TAIL_STEPS = (8192, 65536, 262144)


def subagent_finished(path):
    """True iff the last conversational record is an assistant message that stopped with
    end_turn/stop_sequence (its final answer). Trailing `attachment`/system records written
    after that answer are skipped: measured 2026-10-08, 2 of 300 finished files end that way.
    The tail grows (TAIL_STEPS) until a whole assistant/user record parses: an 8 KB
    tail of a long final answer is ONE partial line, and reading that as "not finished" made
    24% of finished subagents count as running for 15 min.
    Older Claude CLIs write the final answer with stop_reason null; such a subagent reads as
    running until its file is SUBAGENT_STALE_S old."""
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            for step in TAIL_STEPS:
                f.seek(max(0, size - step))
                lines = f.read().decode("utf-8", "replace").splitlines()
                if size > step and lines:
                    lines = lines[1:]          # the first line of a mid-file tail is partial
                for line in reversed(lines):
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    kind = rec.get("type") if isinstance(rec, dict) else None
                    if kind not in ("assistant", "user"):
                        continue
                    return kind == "assistant" and (rec.get("message") or {}).get(
                        "stop_reason") in ("end_turn", "stop_sequence")
                if size <= step:
                    break                      # the whole file was read: nothing conversational
    except OSError:
        return False
    return False
