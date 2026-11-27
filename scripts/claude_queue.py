"""Claude Code's own input queue: what is waiting, read from the session transcript and cross-checked on screen.

MEASURED on Claude Code 2.1.284 (2026-11-27, a lab seat on its own tmux server; see docs/DELIVERY.md "Send now"):

Transcript rows (the witness; file order, not timestamp order, is the truth):
  {"type":"queue-operation","operation":"enqueue","content":<text>}   a message joined the queue
  {"type":"queue-operation","operation":"dequeue"}                    FIFO: the head became a new user turn
  {"type":"queue-operation","operation":"remove","content":<text>,"reason":"absorbed_mid_turn"}
                                                                      that message went into the running turn
  A tool result whose toolUseResult carries backgroundedToDeliverMessage: true = the chord moved a running
  command to the background so the queue could be delivered. An assistant row with stop_reason null right before
  a dequeue, with no system turn_duration row between them = the reply was cut short.
  Task notifications (a background command finishing) are queued too, as "<task-notification>..." content, and
  are never drawn on screen.

Screen (the cross-check): each queued message is a row "❯ " + text in gray (SGR 38;5;246), wrapped rows are two
spaces + gray text, and the block sits directly above the hint row "ctrl+x ctrl+s to send now".

Pure functions over rows and screen text, so tests feed fixtures.
"""
import json
import os
import re
from datetime import datetime

#: How much of the transcript's end is read. Queue rows are recent by nature (the queue drains every turn).
TAIL_BYTES = 8 * 1024 * 1024
HINT = "ctrl+x ctrl+s to send now"
#: Queue content that is not a person's words: submitted with the chord, but never a reason to refuse it.
SYSTEM_PREFIXES = {"<task-notification>": "task-notification"}

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
_GRAY = "38;5;246"


def _norm(s) -> str:
    return " ".join(str(s or "").split())


def same_text(content, text) -> bool:
    """Equal up to runs of whitespace (the CLI and the screen may wrap a line or re-space it), never blind to it:
    "to get her" is not "together". A word the screen breaks mid-word reads as two words and fails (closed)."""
    a = _norm(content)
    return bool(a) and a == _norm(text)


def kind_of(content) -> str:
    c = str(content or "").lstrip()
    for prefix, kind in SYSTEM_PREFIXES.items():
        if c.startswith(prefix):
            return kind
    return "person"


def read_rows(path, tail_bytes=TAIL_BYTES):
    """The transcript's last rows as dicts; [] when there is no file. A partial first line is skipped."""
    if not path:
        return []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > tail_bytes:
                fh.seek(size - tail_bytes)
                fh.readline()
            data = fh.read()
    except OSError:
        return []
    out = []
    for ln in data.decode("utf-8", "replace").splitlines():
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _ts(row):
    try:
        return datetime.fromisoformat(str(row.get("timestamp")).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def queue_items(rows):
    """Every queued item in enqueue order: {content, kind, at, idx, left: None | {how, idx, reason}}. A remove whose
    enqueue is older than the rows read matches nothing and is ignored. A DEQUEUE carries no content, so when the rows
    read start mid-queue (an older item still queued before the tail) it is paired with the oldest item SEEN, which
    may be the wrong one: a caller never trusts "left" on its own (send-now cross-checks the screen)."""
    items, open_ = [], []
    for i, r in enumerate(rows):
        if r.get("type") != "queue-operation":
            continue
        op = r.get("operation")
        if op == "enqueue":
            it = {"content": r.get("content") or "", "kind": kind_of(r.get("content")), "at": _ts(r),
                  "idx": i, "left": None}
            items.append(it)
            open_.append(it)
        elif op == "dequeue" and open_:
            open_.pop(0)["left"] = {"how": "dequeue", "idx": i}
        elif op == "remove":
            for it in open_:
                if it["content"] == (r.get("content") or ""):
                    it["left"] = {"how": "remove", "idx": i, "reason": r.get("reason")}
                    open_.remove(it)
                    break
    return items


#: The remove reasons measured as the message going into the turn (Claude Code 2.1.284).
DELIVERY_REMOVE_REASONS = ("absorbed_mid_turn",)


def left_as_delivery(item) -> bool:
    """The item left the queue BY BEING SENT: a dequeue (it became a turn) or a remove measured as delivery."""
    left = (item or {}).get("left") or {}
    return left.get("how") == "dequeue" or (left.get("how") == "remove"
                                            and left.get("reason") in DELIVERY_REMOVE_REASONS)


def find(rows, text, since=None, until=None):
    """The queue's picture for one message: {"item": the LAST enqueue of this text with its time in [since, until]
    (epoch s; an unbounded side when None), or None; "matches": how many enqueues of it fall in the window (more than
    one = which is this message cannot be told); "others": every OTHER item still queued}. An enqueue with no
    readable time never matches a bounded window."""
    items = queue_items(rows)
    target, matches = None, 0
    for it in items:
        if not same_text(it["content"], text):
            continue
        if since is not None or until is not None:
            if it["at"] is None or (since is not None and it["at"] < since) or (until is not None and it["at"] > until):
                continue
        target = it
        matches += 1
    others = [it for it in items if it["left"] is None and it is not target]
    return {"item": target, "matches": matches, "others": others}


def step_ended(rows, item, since=None) -> str:
    """What the delivery of a queued item did to the step that was running, from the rows only:
    "backgrounded" (a running command was moved to the background to deliver it), "truncated" (the reply being
    written was cut short and the queue started a new turn), "none" (it went in at a boundary the turn reached on
    its own), or "unknown" when the rows do not say. `since` (epoch s, the moment the keys went): a tool result older
    than that is no evidence of what the keys did, so only rows at or after it count on the remove path."""
    left = (item or {}).get("left")
    if not left:
        return "unknown"
    j = left["idx"]
    k = next((n for n in range(j + 1, len(rows)) if _is_prompt_row(rows[n])), None)
    fresh = lambda r: since is None or ((_ts(r) or 0) >= since)
    if left["how"] == "remove":
        # The background row may be written before or after the remove row (the order is not pinned): read both
        # sides, up to the next prompt (or the end).
        window = [r for r in rows[item["idx"]:(k if k is not None else len(rows))] if fresh(r)]
        results = [r.get("toolUseResult") for r in window if r.get("type") == "user"
                   and isinstance(r.get("toolUseResult"), dict)]
        if any(t.get("backgroundedToDeliverMessage") is True for t in results):
            return "backgrounded"
        before = [r for r in rows[item["idx"]:j] if fresh(r) and r.get("type") == "user"
                  and isinstance(r.get("toolUseResult"), dict)]
        return "none" if before else "unknown"
    # The ending turn's last rows are written AFTER the dequeue row (measured): read the rows between the dequeue
    # and the new turn's prompt first, newest first, then the rows before the dequeue. With no prompt row after the
    # dequeue yet, the rows after it may already be the NEW turn's: only the rows before it are read.
    after = list(reversed(rows[j + 1:k])) if k is not None else []
    for r in after + list(reversed(rows[:j])):
        if r.get("type") == "system" and r.get("subtype") == "turn_duration":
            return "none"                       # the turn had ended: an ordinary drain
        if r.get("type") == "assistant":
            stop = (r.get("message") or {}).get("stop_reason")
            return "truncated" if stop is None else "unknown"
        if _is_prompt_row(r):
            return "unknown"                    # a turn's start: nothing between says how the last one ended
    return "unknown"


def _is_prompt_row(r) -> bool:
    return r.get("type") == "user" and isinstance((r.get("message") or {}).get("content"), str)


def screen_queue(raw):
    """The queued messages drawn above the send-now hint, as text (wrapped rows joined), or None when no hint is
    on screen. `raw` is a capture WITH escapes: the gray is what tells a queued row from a submitted one."""
    lines = (raw or "").splitlines()
    plain = [_ANSI.sub("", ln) for ln in lines]
    hint = [i for i, p in enumerate(plain) if p.strip() == HINT]
    if not hint:
        return None
    def queued_row(i):
        return plain[i].startswith("❯ ") and _GRAY in lines[i]

    def cont_row(i):
        p = plain[i]
        return p.startswith("  ") and p[2:3] not in ("", " ", "⎿") and lines[i].lstrip(" ").startswith(f"\x1b[{_GRAY}m")

    out, cont = [], []
    for i in range(hint[-1] - 1, -1, -1):
        p, r = plain[i], lines[i]
        if not p.strip() and not cont and out and i > 0 and (queued_row(i - 1) or cont_row(i - 1)):
            continue                            # the blank row the CLI draws between two queued messages
        if queued_row(i):
            out.append(" ".join([p[2:].strip()] + list(reversed(cont))).strip())
            cont = []
        elif cont_row(i):
            cont.append(p.strip())
        else:
            break
    if cont:
        out.append(" ".join(reversed(cont)))    # gray rows with no queue row above them: counted, never dropped
    return list(reversed(out))
