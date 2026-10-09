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

import re

from .brain import render_transcript, tool_protocol_block, parse_cli_reply
from .cli_events import (stream_command, warm_command, WARM_RUNTIMES, read_events,
                         TextDelta, ThinkingDelta, TurnEnd, StreamError)
from .stream_sanitize import PassClassifier, StreamingSanitizer, HOLD, PROSE, batch_sanitize


#: Same ceiling as the /text tool loop (chat_completions MAX_TOOL_ROUNDS).
MAX_TOOL_ROUNDS = 5

_TOOL_ENVELOPE_RE = re.compile(r'\{\s*"tool_calls"')
_ENVELOPE_PREFIX = '{"tool_calls"'


def _prose_safe_len(raw: str) -> int:
    """How much of a prose pass may be shown: everything before a tool envelope, and before a
    trailing fragment that could still BECOME one. A model that says "Let me check." and then
    emits {"tool_calls": ...} in the same pass must never show the JSON — and the brace can
    arrive split across deltas ('{"tool_ca' + 'lls"...')."""
    m = _TOOL_ENVELOPE_RE.search(raw)
    if m:
        return m.start()
    brace = raw.rfind("{")
    if brace >= 0:
        tail = re.sub(r"\s+", "", raw[brace:])
        if _ENVELOPE_PREFIX.startswith(tail):
            return brace
    return len(raw)


def sse_frame(event: dict) -> str:
    """One SSE frame: a named event with a JSON payload."""
    return f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"


def _ev(event_name, **data):
    # `event_name`, not `name`: a tool event carries a `name` field of its own.
    return {"event": event_name, "data": data}


def stream_turn(text, conversation_id, brain, brain_id, messages, spawn, fallback, record,
                tools=None, turn_id=None, warm=None, discard=None, run_tools=None):
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
    model_flag = getattr(brain, "_model_flag", "") or ""
    if warm is not None and run_tools is not None and brain.runtime in WARM_RUNTIMES:
        yield from _warm_tool_loop(turn_id, text, conversation_id, brain, brain_id, messages,
                                   tools, warm, discard, run_tools, fallback, record)
        return
    if warm is not None and brain.runtime in WARM_RUNTIMES:
        # A warm process already remembers the conversation, so it gets ONLY the new message.
        source = warm(warm_argv(brain, messages[:-1], tools), messages[-1].get("content") or "")
    else:
        source = spawn(stream_command(brain.runtime, brain.cli, system, prompt, model=model_flag))
    classifier, sanitizer = PassClassifier(), StreamingSanitizer()
    verdict, held, fed, failure = None, "", 0, None

    try:
        for event in read_events(brain.runtime, source):
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
                if _TOOL_ENVELOPE_RE.search(held):
                    break              # prose that turned into a tool call: /text runs the tools
                # Only what precedes a possible envelope: '{"tool_ca' can still become one.
                safe = _prose_safe_len(held)
                if safe > fed:
                    out = sanitizer.feed(held[fed:safe])
                    fed = safe
                    if out:
                        yield _ev("text.delta", turn_id=turn_id, text=out)
                continue
            if isinstance(event, TurnEnd):
                # truncated = the CLI's output stopped before its result line: half an answer.
                if verdict != PROSE or event.truncated:
                    break
                out = sanitizer.feed(held[fed:])      # a held brace that never became an envelope
                if out:
                    yield _ev("text.delta", turn_id=turn_id, text=out)
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

    # From here the warm process's memory no longer matches the conversation: it either failed,
    # or it produced a tool envelope whose real answer the tool loop is about to write. Reusing
    # it would continue a conversation that did not happen, so it is thrown away.
    if discard is not None:
        discard()

    if failure is not None:
        yield _ev("error", turn_id=turn_id, code=failure.code, message=failure.message)
        return

    # HOLD, or a stream that stopped before it finished: the ordinary path owns this turn.
    yield from _fallback_reply(turn_id, conversation_id, fallback, brain)


def _warm_tool_loop(turn_id, text, conversation_id, brain, brain_id, messages, tools,
                    warm, discard, run_tools, fallback, record):
    """The tool loop, inside the conversation's warm process.

    Each pass is one turn of the same process. A pass that asks for tools has them executed
    (run_tools -> execute_tool, with the turn's dedupe ledger and the send_telegram check), and
    the results go back to the SAME process in the protocol's `TOOL RESULT (name): ...` form, so
    its memory stays true and nothing is thrown away. Prose streams in every pass — including
    the sentence a model says before it reaches for a tool — and the envelope never does.
    """
    argv = warm_argv(brain, messages[:-1], tools)
    next_message = messages[-1].get("content") or ""
    shown, tools_called = "", []
    n_calls = 0

    for _round in range(MAX_TOOL_ROUNDS + 1):
        classifier, sanitizer = PassClassifier(), StreamingSanitizer()
        verdict, raw, fed, pass_out = None, "", 0, ""
        failure, ended = None, False
        separator_pending = bool(shown)

        def emit(out):
            nonlocal pass_out, shown, separator_pending
            if not out:
                return None
            if separator_pending:
                out = "\n\n" + out
                separator_pending = False
            pass_out += out
            shown += out
            return _ev("text.delta", turn_id=turn_id, text=out)

        try:
            for event in read_events(brain.runtime, warm(argv, next_message)):
                if isinstance(event, ThinkingDelta):
                    yield _ev("thinking.delta", turn_id=turn_id, text=event.text)
                elif isinstance(event, StreamError):
                    failure = event
                    break
                elif isinstance(event, TextDelta):
                    raw += event.text
                    if verdict is None:
                        verdict = classifier.feed(event.text)
                        if verdict is None:
                            continue
                    if verdict == PROSE:
                        safe = _prose_safe_len(raw)
                        if safe > fed:
                            frame = emit(sanitizer.feed(raw[fed:safe]))
                            fed = safe
                            if frame:
                                yield frame
                elif isinstance(event, TurnEnd):
                    # A truncated end is the process dying mid-pass: not ended, so the pass
                    # falls back rather than recording half an answer as the reply.
                    ended = not event.truncated
                    break
        except Exception as e:                  # noqa: BLE001 — a broken pipe is a failed turn
            failure = StreamError("stream_failed", str(e)[:200])

        if failure is not None or not ended:
            if discard is not None:
                discard()
            if failure is not None:
                yield _ev("error", turn_id=turn_id, code=failure.code, message=failure.message)
                return
            yield from _fallback_reply(turn_id, conversation_id, fallback, brain)
            return

        # What is left of the pass: prose held back, and possibly a tool envelope.
        cut = _prose_safe_len(raw) if verdict == PROSE else 0
        if verdict == PROSE:
            frame = emit(sanitizer.feed(raw[fed:cut]))
            if frame:
                yield frame
            frame = emit(sanitizer.finish())
            if frame:
                yield frame
        structure = raw[cut:] if verdict == PROSE else raw
        calls = []
        if structure.strip():
            parsed = parse_cli_reply(structure).choices[0].message
            for tc in parsed.tool_calls or []:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                calls.append({"name": tc.function.name, "arguments": args})
            if not calls and parsed.content and verdict != PROSE:
                # A held pass that turned out to be prose after all (or the native-markup
                # fallback sentence): it is the answer, sanitised like any other.
                frame = emit(batch_sanitize(parsed.content))
                if frame:
                    yield frame

        if not calls:
            reply = shown.strip()
            if not reply:
                if discard is not None:
                    discard()
                yield _ev("error", turn_id=turn_id, code="empty_response", message="the model returned nothing")
                return
            record(conversation_id=conversation_id, text=text, reply=reply, brain=brain_id)
            yield _ev("turn.end", turn_id=turn_id, conversation_id=conversation_id, reply_text=reply,
                      tools_called=tools_called, brain=brain.describe())
            return

        if _round == MAX_TOOL_ROUNDS:
            break

        # Each call is its own card on the client, closed by its own result. Results pair with
        # calls by id, not by position — two calls to one tool in a pass made position a guess.
        # run_tools answers in call order, so the id rides from call to result by zip.
        for c in calls:
            n_calls += 1
            c["call_id"] = f"c{n_calls}"
            yield _ev("tool.call", turn_id=turn_id, call_id=c["call_id"], name=c["name"],
                      args_summary=json.dumps(c["arguments"], ensure_ascii=False)[:200])
        results = run_tools([{"name": c["name"], "arguments": c["arguments"]} for c in calls])
        for c, r in zip(calls, results):
            tools_called.append(r["name"])
            yield _ev("tool.result", turn_id=turn_id, call_id=c["call_id"], name=r["name"],
                      ok=bool(r.get("ok")), summary=str(r.get("result"))[:200])
        next_message = "\n".join(f"TOOL RESULT ({r['name']}): {r.get('result')}" for r in results)

    # Still asking for tools after the ceiling: the ordinary path owns this turn.
    if discard is not None:
        discard()
    yield from _fallback_reply(turn_id, conversation_id, fallback, brain)


def warm_argv(brain, prior_messages, tools=None):
    """argv for a conversation's warm process, from everything BEFORE the new message.

    The system prompt is Arturo's context plus the tool protocol, rendered exactly as
    RuntimeBrain renders it; the history rides in it because a warm process takes only new
    messages as turns. Shared by the turn and the prewarm, so a prewarmed process is the same
    process the turn would have started — a prewarm that built a different prompt would be
    reused for a conversation it does not match.
    """
    system, history_prompt = render_transcript(prior_messages)
    block = tool_protocol_block(tools or [], None)
    if block:
        system = f"{system}\n\n{block}" if system else block
    if history_prompt.strip():
        system = f"{system}\n\nThe conversation so far:\n{history_prompt}"
    return warm_command(brain.runtime, brain.cli, system, getattr(brain, "_model_flag", "") or "")


def _fallback_reply(turn_id, conversation_id, fallback, brain):
    """The whole-reply path taking over a turn that may already have shown text or tool cards.

    `turn.reset` first: the whole reply repeats the answer from the top, so whatever the dead
    attempt put on screen has to go, or the operator reads half a reply, orphaned tool rows,
    and then the whole reply beneath them. A client with nothing shown treats it as a no-op."""
    yield _ev("turn.reset", turn_id=turn_id, reason="fallback")
    yield from _whole_reply(turn_id, conversation_id, fallback, brain)


class ToolEvents:
    """What a whole turn's tool loop reports as it works: each call opens a card, each result
    closes it. Ids run c1, c2… across the turn, the same shape as the warm loop's events."""

    def __init__(self, emit):
        self._emit = emit
        self._n = 0

    def call(self, name, arguments):
        self._n += 1
        call_id = f"c{self._n}"
        self._emit("tool.call", call_id=call_id, name=name,
                   args_summary=json.dumps(arguments or {}, ensure_ascii=False)[:200])
        return call_id

    def result(self, call_id, name, ok, summary):
        self._emit("tool.result", call_id=call_id, name=name, ok=bool(ok), summary=str(summary)[:200])


def whole_turn(conversation_id, brain, fallback, turn_id=None, run_with_sink=None):
    """A turn served whole on the streaming endpoint: turn.start, then the ordinary path's
    reply as one delta. For a turn only /text knows how to run — an onboarding step, whose
    marker and directive text_turn applies — or a runtime with no streaming tool loop (codex).

    `run_with_sink(sink)` runs the turn in a worker and returns what `fallback()` would; its
    tool loop reports calls and results to `sink` as they happen, and they stream out here
    live, so a whole turn still shows its tool cards before its answer."""
    turn_id = turn_id or f"turn_{uuid.uuid4().hex[:12]}"
    yield _ev("turn.start", turn_id=turn_id, conversation_id=conversation_id,
              brain=brain.describe(), at=time.time())
    if run_with_sink is None:
        yield from _whole_reply(turn_id, conversation_id, fallback, brain)
        return
    q, box = queue.Queue(), {}
    sink = ToolEvents(lambda event, **data: q.put(_ev(event, turn_id=turn_id, **data)))

    def work():
        try:
            box["r"] = run_with_sink(sink)
        except BaseException as e:  # noqa: BLE001 — reported below as the turn's error
            box["e"] = e
        finally:
            q.put(_SENTINEL)

    threading.Thread(target=work, daemon=True, name=f"whole-turn-{turn_id}").start()
    while True:
        item = q.get()
        if item is _SENTINEL:
            break
        yield item
    if "e" in box:
        yield _ev("error", turn_id=turn_id, code="turn_failed", message=str(box["e"])[:200])
        return
    yield from _whole_reply(turn_id, conversation_id, lambda: box["r"], brain)


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
              **({"operator": body["operator"]} if "operator" in body else {}),
              **{k: body[k] for k in ("team", "choices", "pair_card", "paired", "onboarding") if k in body},
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
