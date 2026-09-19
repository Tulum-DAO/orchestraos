# Arturo reply fixtures

Operator-facing replies that leaked raw internals. Kept as FILES, read by path, and never
pasted into mail, PR bodies or handoffs: the repo's court-guard Stop hook cannot tell a
document QUOTING this markup from an agent emitting it, so quoting the evidence raises a
false alarm for the reader. Break the opening tag if you must show it in prose.

## `native_tool_markup_reply.txt`
The claude CLI answering in its own tool-call syntax instead of the JSON envelope the brain
prompt asks for, which `parse_cli_reply` then handed back as the assistant's TEXT.

PROVENANCE, stated exactly: observed 2026-09-19T08:42Z in container `art-rc` (image built
from release sha 4ec0229) by typing the run-of-show's beat-3.1 prompt. This file is a faithful
TRANSCRIPTION of that reply, cross-checked against the same bytes as stored in the thread
`snippet` returned by `GET /api/arturo/threads` — it is NOT a byte-capture saved at the time,
because the container was torn down before the fixture was asked for. Three later attempts to
re-capture it (2026-09-19, container `art-cap`, same image and prompt) came back clean, which
is itself the point: the defect is a DICE ROLL, not a deterministic failure.

## `runtime_error_reply.txt`
A genuine byte-capture (container `art-cap`, attempt 1 of those three). When the brain
subprocess fails, `RuntimeBrain.complete` put `str(e)` straight into the operator's reply —
and a `CalledProcessError` stringifies to the whole argv, which carries `--system-prompt`
followed by Arturo's entire system prompt. Truncation to 200 chars does not save it; it just
cuts off mid-prompt. It persisted as the thread snippet too.
