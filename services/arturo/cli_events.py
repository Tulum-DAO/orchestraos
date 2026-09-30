"""Read each CLI's OWN typed streaming events (spec §0.13).

A streamed turn does not have to infer structure from characters: every CLI we ship with can
say what it is emitting. Their shapes differ, so each runtime gets a small reader, and all
three produce the SAME internal events — which is what the SSE endpoint serialises.

    claude 2.1.284   `--output-format stream-json --verbose --include-partial-messages`
                     API-shaped: stream_event -> content_block_delta -> text_delta,
                     with thinking deltas interleaved on their own channel.
    agy 1.2.13       `--output-format stream-json` -> step_update.text_delta, result at the end.
    codex 0.153.4    `exec --json` -> item.completed with the WHOLE message. No token deltas
                     exist, so codex emits one final delta marked `incremental=False` rather
                     than pretending to type.

Everything here is derived from output captured from the installed CLIs and kept in
fixtures/cli_streams/, so a CLI that changes its shape fails a test rather than a live turn.

What this module does NOT decide: whether the text is safe to show. Our tool protocol asks a
CLI for either a JSON `{"tool_calls": ...}` reply or prose (§0.7), so assistant text may BE a
tool envelope — that is stream_sanitize.PassClassifier's job, downstream of these events.
"""
import json
from dataclasses import dataclass, field
from typing import Iterator, Optional

from .brain import CommandSpec


@dataclass
class TextDelta:
    text: str
    #: False when the CLI only ever hands over a finished message (codex).
    incremental: bool = True


@dataclass
class ThinkingDelta:
    text: str


@dataclass
class TurnEnd:
    reply_text: str
    #: True when the stream stopped before the CLI said it was finished.
    truncated: bool = False
    raw: dict = field(default_factory=dict)


@dataclass
class StreamError:
    code: str
    message: str


def stream_command(runtime: str, cli: str, system: str, prompt: str, model: str = "") -> CommandSpec:
    """The streaming twin of brain.runtime_command. Same tool-less, session-less invocation."""
    if runtime == "claude":
        # --verbose is REQUIRED with stream-json (measured: the CLI refuses without it), and
        # --include-partial-messages is what turns "one final message" into token deltas.
        argv = [cli, "-p", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--no-session-persistence", "--tools", "",
                "--strict-mcp-config", "--setting-sources", "", "--system-prompt", system]
        if model:
            argv.append(f"--model={model}")
        return CommandSpec(argv=argv, stdin=prompt,
                           env_unset=["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"])
    if runtime == "gemini":
        # --print swallows the next bare arg as the prompt, so it is attached with `=`.
        argv = [cli, f"--print={system}\n\n{prompt}", "--output-format", "stream-json"]
        if model:
            argv.append(f"--model={model}")
        return CommandSpec(argv=argv, stdin=None)
    if runtime == "codex":
        argv = [cli, "exec", "--json", "--skip-git-repo-check", "--ephemeral",
                "-s", "read-only", "--color", "never"]
        if model:
            argv.append(f"--model={model}")
        return CommandSpec(argv=argv, stdin=f"{system}\n\n{prompt}")
    raise ValueError(f"no streaming command for runtime {runtime!r}")


def _json_lines(lines):
    for line in lines:
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue                      # a partial or non-JSON line is not a turn failure


def read_events(runtime: str, lines: Iterator[str]):
    """Translate one CLI's stdout lines into the internal event stream."""
    reader = {"claude": _read_claude, "gemini": _read_agy, "codex": _read_codex}.get(runtime)
    if reader is None:
        yield StreamError("unknown_runtime", f"no streaming reader for {runtime!r}")
        return
    saw_any = False
    for event in reader(_json_lines(lines)):
        saw_any = True
        yield event
    if not saw_any:
        # Silence is not an empty answer: a turn that produced nothing has failed.
        yield StreamError("empty_stream", f"{runtime} produced no events")


def _read_claude(objs):
    text, closed = [], False
    for obj in objs:
        if obj.get("type") == "stream_event":
            ev = obj.get("event") or {}
            kind = ev.get("type")
            if kind == "content_block_delta":
                delta = ev.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    text.append(delta["text"])
                    yield TextDelta(delta["text"])
                elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                    yield ThinkingDelta(delta["thinking"])
            elif kind == "message_stop":
                closed = True
        elif obj.get("type") == "result":
            if obj.get("is_error"):
                yield StreamError("cli_error", str(obj.get("result") or "runtime error"))
                return
            yield TurnEnd("".join(text), truncated=False, raw=obj)
            return
    if text or closed:
        yield TurnEnd("".join(text), truncated=True)


def _read_agy(objs):
    text = []
    for obj in objs:
        if obj.get("event") == "step_update":
            step = obj.get("step_update") or {}
            if step.get("step_type") == "agent_response" and step.get("text_delta"):
                text.append(step["text_delta"])
                yield TextDelta(step["text_delta"])
        elif obj.get("event") == "result":
            result = obj.get("result") or {}
            if str(result.get("status", "")).upper() not in ("SUCCESS", ""):
                yield StreamError("cli_error", str(result.get("status")))
                return
            yield TurnEnd(result.get("response") or "".join(text), truncated=False, raw=result)
            return
    if text:
        yield TurnEnd("".join(text), truncated=True)


def _read_codex(objs):
    """codex has no token deltas: the message arrives whole, once (§0.13)."""
    text = []
    for obj in objs:
        msg = obj.get("msg") or obj
        kind = msg.get("type")
        if kind == "item.completed":
            item = msg.get("item") or {}
            # `error` items are operational notices (a skills-budget warning in the capture)
            # and are NOT the assistant speaking.
            if item.get("type") == "agent_message" and item.get("text"):
                text.append(item["text"])
                yield TextDelta(item["text"], incremental=False)
        elif kind == "turn.failed":
            yield StreamError("cli_error", str(msg.get("error") or "turn failed"))
            return
        elif kind == "turn.completed":
            yield TurnEnd("".join(text), truncated=False, raw=msg)
            return
    if text:
        yield TurnEnd("".join(text), truncated=True)
