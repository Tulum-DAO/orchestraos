"""transcript_turn — the E2 turn-completion signal from the claude transcript.

The missed-Stop residual: Claude Code emits NO Stop hook when a turn ends by
interrupt (send-keys racing the turn boundary) or process kill mid-turn (rotation
SIGKILL), so state/agent-events/panes/<pane>.json freezes at state="working" and a
genuinely-idle seat reads `stalled`. The hook layer CANNOT distinguish that from a
real mid-tool hang (both look like {working hook, 0 CPU, 0 bytes}). The claude
transcript CAN: its last real message says whether the turn COMPLETED or is OPEN.

`transcript_turn_complete(path)` returns:
  True  -> the last real message is a COMPLETED assistant turn (end_turn / stop,
           no pending tool_use) => idle even though no Stop hook fired.
  False -> the turn is OPEN (assistant still awaiting a tool it requested, or a
           trailing user tool_result / fresh prompt) => genuine working/stall — do
           NOT mask it.
  None  -> unknown (no path / absent / unreadable / no assistant|user message in
           the tail) => caller falls back to trusting the hook (no regression).

Reads only the FILE TAIL (bounded) so a multi-MB transcript costs a cheap seek+read,
never a full parse.
"""
import json
import os

_TAIL_BYTES = 65536
# stop_reasons that mean the assistant finished its turn (no more tool calls).
_COMPLETE_STOPS = {"end_turn", "stop", "stop_sequence", "max_tokens"}


def _last_real_message(path, tail_bytes):
    """The last {assistant|user} message dict in the transcript tail, or None."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    try:
        with open(path, "rb") as fh:
            if size > tail_bytes:
                fh.seek(size - tail_bytes)
                raw = fh.read()
                # a tail read can slice the first line mid-JSON -> drop it.
                nl = raw.find(b"\n")
                raw = raw[nl + 1:] if nl != -1 else b""
            else:
                raw = fh.read()
    except OSError:
        return None
    last = None
    for line in raw.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue                          # partial / malformed line -> skip
        if not isinstance(entry, dict):
            continue
        if entry.get("type") not in ("assistant", "user"):
            continue                          # meta (ai-title, mode, ...) -> ignore
        msg = entry.get("message")
        if isinstance(msg, dict):
            last = (entry.get("type"), msg)
    return last


def transcript_turn_complete(path, tail_bytes=_TAIL_BYTES):
    """True=completed turn (idle), False=open turn (working/stall), None=unknown."""
    if not path:
        return None
    found = _last_real_message(path, tail_bytes)
    if found is None:
        return None
    kind, msg = found
    if kind == "user":
        return False                          # tool_result pending, or unanswered prompt
    # assistant: OPEN if it requested a tool still awaiting a result.
    if msg.get("stop_reason") == "tool_use":
        return False
    content = msg.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                return False                  # tool_use present => turn not finished
    if msg.get("stop_reason") in _COMPLETE_STOPS:
        return True
    # assistant text with an unrecognized/absent stop_reason and no tool_use:
    # treat as complete (a finished textual turn) — the safe idle direction only
    # ever fires here when the hook already said working with zero activity.
    return True
