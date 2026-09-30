"""/text/stream — the turn as it happens (spec §2.1-§2.5).

`/text` is untouched and still answers whole replies; this is the opt-in endpoint for clients
that want the words as they arrive.

Three rules shape it:

1. **Tools are not forked.** Our tool protocol asks a CLI for either a JSON `{"tool_calls": …}`
   reply or prose (§0.7), so a streamed reply may turn out to BE a tool envelope. The first
   characters decide (stream_sanitize.PassClassifier): prose streams; STRUCTURE is discarded
   unshown and the turn is re-run down the ordinary `/text` path, tool loop and all. The voice
   loop — Change B's region — is not touched, and tool behaviour cannot drift from `/text`
   because it IS `/text`.

2. **Nothing reaches the client unsanitised.** Deltas go through StreamingSanitizer, whose
   output is byte-identical to what `/text` would have saved.

3. **The thread is recorded once, through the same function `/text` uses.** A streamed turn and
   a whole turn leave the same row. The fallback path records itself, so this module does not.

`turn.start` is emitted on receipt, which is what lets the client show "Sent" on a server fact
rather than on an upload event a streaming fetch does not have (§2.5 P3).
"""
import json
import queue
import threading
import time
import uuid

from .brain import render_transcript, tool_protocol_block
from .cli_events import stream_command, read_events, TextDelta, ThinkingDelta, TurnEnd, StreamError
from .stream_sanitize import PassClassifier, StreamingSanitizer, HOLD, PROSE


def sse_frame(event: dict) -> str:
    """One SSE frame: a named event with a JSON payload."""
    return f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"


def _ev(name, **data):
    return {"event": name, "data": data}


def stream_turn(text, conversation_id, brain, brain_id, messages, spawn, fallback, record,
                tools=None, turn_id=None):
    """Yield the turn's events. Pure over its injected deps so the tests never spawn a CLI.

    spawn(CommandSpec) -> iterator of stdout lines; fallback() -> (status, body) from the
    ordinary /text turn; record(**kw) -> persist a streamed turn.
    """
    turn_id = turn_id or f"turn_{uuid.uuid4().hex[:12]}"
    yield _ev("turn.start", turn_id=turn_id, conversation_id=conversation_id,
              brain=brain.describe(), at=time.time())

    # An API brain has no CLI to stream; say so by behaviour, not by pretending to type.
    if getattr(brain, "kind", "") != "runtime":
        yield from _whole_reply(turn_id, conversation_id, fallback, brain)
        return

    # The SAME rendering RuntimeBrain._text uses, so a streamed turn reaches the CLI with the
    # identical system prompt (Arturo's live context) and the identical tool protocol. Building
    # a second, simpler prompt here is exactly how a streamed turn ends up answered by a bare
    # model that has never heard of Arturo or its tools (found on staging, 2026-09-29).
    system, prompt = render_transcript(messages)
    block = tool_protocol_block(tools or [], None)
    if block:
        system = f"{system}\n\n{block}" if system else block
    cmd = stream_command(brain.runtime, brain.cli, system, prompt,
                         model=getattr(brain, "_model_flag", "") or "")
    classifier, sanitizer = PassClassifier(), StreamingSanitizer()
    verdict, held, failure = None, "", None

    try:
        for event in read_events(brain.runtime, spawn(cmd)):
            if isinstance(event, ThinkingDelta):
                yield _ev("thinking.delta", turn_id=turn_id, text=event.text)
                continue
            if isinstance(event, StreamError):
                failure = event
                break
            if isinstance(event, TextDelta):
                held += event.text
                if verdict is None:
                    verdict = classifier.feed(event.text)
                    if verdict == HOLD:
                        break          # structure: never shown, re-run down /text
                    if verdict is None:
                        continue       # undecided — nothing is safe to show yet
                    out = sanitizer.feed(held)
                else:
                    out = sanitizer.feed(event.text)
                if out:
                    yield _ev("text.delta", turn_id=turn_id, text=out)
                continue
            if isinstance(event, TurnEnd):
                if verdict != PROSE:
                    break
                tail = sanitizer.finish()
                if tail:
                    yield _ev("text.delta", turn_id=turn_id, text=tail)
                reply = sanitizer._sent
                if not reply.strip():
                    # An empty reply is a failed turn, not an empty bubble.
                    yield _ev("error", turn_id=turn_id, code="empty_response",
                              message="the model returned nothing")
                    return
                record(conversation_id=conversation_id, text=text, reply=reply, brain=brain_id)
                yield _ev("turn.end", turn_id=turn_id, conversation_id=conversation_id,
                          reply_text=reply, tools_called=[], brain=brain.describe())
                return
    except Exception as e:                      # a broken pipe mid-turn is a failed turn
        failure = StreamError("stream_failed", str(e)[:200])

    if failure is not None:
        yield _ev("error", turn_id=turn_id, code=failure.code, message=failure.message)
        return

    # HOLD, or a stream that stopped before it finished: the ordinary path owns this turn.
    yield from _whole_reply(turn_id, conversation_id, fallback, brain)


def _whole_reply(turn_id, conversation_id, fallback, brain):
    status, body = fallback()
    if status != 200 or not body.get("ok"):
        yield _ev("error", turn_id=turn_id, code=body.get("error", "turn_failed"),
                  message=body.get("reason") or body.get("detail") or "",
                  tools_called=body.get("tools_called", []))
        return
    reply = body.get("reply_text") or ""
    if reply:
        yield _ev("text.delta", turn_id=turn_id, text=reply)
    yield _ev("turn.end", turn_id=turn_id, conversation_id=conversation_id, reply_text=reply,
              tools_called=body.get("tools_called", []),
              spawned=body.get("spawned", []),
              brain=body.get("brain") or brain.describe())


_SENTINEL = object()


def with_heartbeat(events, interval_s=10.0):
    """Yield SSE frames, filling any silence with `: ping` comments (spec §2.2).

    A turn can legitimately produce nothing for a while — the tool loop runs with no output,
    and a large model can take seconds before its first token. Every hop in front of us drops
    an idle socket (the dashboard proxy at 30s), so silence reads as a dead connection and the
    browser gets a 502 mid-turn. This was found live, not in review.

    The turn runs in a worker thread so the ping can be emitted while the turn is blocked;
    nothing about the turn itself becomes concurrent.
    """
    q: "queue.Queue" = queue.Queue()

    def pump():
        try:
            for event in events:
                q.put(("event", event))
        except Exception as e:  # noqa: BLE001 — a dead turn must still tell the client why
            q.put(("error", e))
        finally:
            q.put(("done", _SENTINEL))

    worker = threading.Thread(target=pump, daemon=True)
    worker.start()
    while True:
        try:
            kind, payload = q.get(timeout=interval_s)
        except queue.Empty:
            yield ": ping\n\n"          # a comment: valid SSE, ignored by every client
            continue
        if kind == "event":
            yield sse_frame(payload)
        elif kind == "error":
            yield sse_frame(_ev("error", code="stream_failed", message=str(payload)[:200]))
            return
        else:
            return
