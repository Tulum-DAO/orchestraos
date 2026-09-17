# Composer state — reading a pane's input line honestly

Every agent runs in a terminal (tmux pane) driving a CLI (Claude Code, Gemini CLI, Codex).
The **composer** is that CLI's input line — where a human types before pressing Enter. A lot
of fleet logic turns on one question: *is there unsubmitted human input in this pane's
composer right now?* If yes, you must not swap the seat out, clobber the line, or press
Enter for the human. If no, those actions are safe.

The trap is that the composer line often contains text that is **not** human input.

## What a ghost is (and why it matters)

A **ghost suggestion** is text the CLI itself renders in the composer — an autocomplete, a
continuation, or an empty-composer placeholder — drawn in **dim** styling (ANSI SGR `2m`) to
mark it as "not yours yet." It looks like a typed line but no human typed it.

Real example (Claude): the composer showed a full sentence the model had suggested, e.g.

> `yes example.com is primary now, remove the old domain from search console`

Both a human reader and the fleet read that as an unsubmitted instruction. It was a ghost —
the LLM's own suggested continuation. Acting on it (delivering around it, holding a swap on
it, or pressing Enter) would have submitted the model's words as if the operator had typed
them.

So the rules are:

- **Never clobber a ghost** — it is not work to preserve.
- **Never treat a ghost as pending input** — do not hold delivery or a rotation swap on it.
- **Never Enter a ghost** — submitting it sends the model's suggestion as a human message.

The failure came from *how* the composer was read: several readers captured the pane with
`tmux capture-pane -p` **without `-e`**, which strips the SGR codes. Once the dim styling is
gone, a ghost is indistinguishable from typed text, and a content-ghost (not just a "Try…"
placeholder) reads as a real instruction.

## How to read it — `scripts/composer_state.py`

One module, one function, every reader calls it:

```python
from scripts.composer_state import read_composer

r = read_composer(pane)            # pane = tmux session/target; runtime auto-resolved
r = read_composer(pane, runtime="claude")   # or pass the known runtime

# r == {
#   "state":   "empty" | "ghost" | "typed" | "submitted" | "unknown",
#   "text":    the human-typed text (only when state == "typed", else ""),
#   "runtime": "claude" | "gemini" | "codex" | "unknown",
#   "evidence": {"line": <the composer line>, "why": ...},
# }
```

| state | meaning | safe to swap / deliver? | Enter it? |
|-------|---------|-------------------------|-----------|
| `empty` | bare prompt, nothing after it | yes | n/a |
| `ghost` | dim suggestion/placeholder (LLM text, not human) | yes | **never** |
| `typed` | non-dim characters after the prompt = human input | **no — preserve it** | only the human |
| `submitted` | the runtime is mid-turn / working | **no — turn in flight** | n/a |
| `unknown` | unreadable pane, or an unrecognized runtime | **no — fail closed** | never |

`unknown` is deliberate: an unrecognized runtime is **never** classified `typed`. We do not
know its ghost rendering, so we refuse to guess — a wrong `typed` silently holds the fleet,
a wrong `empty` clobbers human work, both are the "success that isn't" class.

### How the classifier works

`read_composer` captures the pane with `tmux capture-pane -e -p` (the `-e` **preserves** the
SGR codes — the whole point), then walks the bottom-most prompt line tracking dim (`2m`) and
reverse-video (`7m`, the cursor block) state, keyed on the per-runtime prompt signature in
`scripts/runtime_signatures.py`:

- **Claude** (`❯`): dim words = ghost; reverse-video = cursor; any default-styled char = typed.
- **Codex** (`›`): same dim mechanism; the idle placeholder "Ask Codex to do anything" is dim = ghost.
- **Gemini** (`>`): no dim ghost class — any non-space visible char after `>` is typed.

The walk is the one ported from `message-router.py:input_line_state`, the reader that already
got this right; `composer_state` makes it the single source for every reader.

### The stale-screen rule

A composer probe can read the **old** screen right after you send a key, until a printable
key lands (a known tmux/TUI redraw lag). If you have just sent `Enter` or `C-u` to a pane and
need to re-read it, use `settle_and_read(pane)`, which sends a `space` then `BSpace` (a no-op
printable + delete) to force a redraw before reading. This is only for callers **actively
driving** a pane (e.g. the rotation readiness probe after a `C-u` clear) — never for passive
observation, and never on a pane a human is attached to.

## Fixtures

The fixtures in `scripts/test_composer_state.py` carry real `capture-pane -e` SGR taken by
effect from live CLIs (gemini idle, codex idle placeholder, claude bare prompt). The Claude
ghost/typed fixtures are built from the same verified SGR-dim grammar — the identical
`ESC[2m` mechanism the real codex placeholder proves on live data. Live Claude
content-ghosts are transient (they clear when submitted), so the operator-cited line is
reproduced with a neutral example sentence rather than re-captured.

## Runtime gaps

- **Gemini** renders no dim ghost class, so it has no ghost/typed ambiguity — but it also
  cannot show a suggestion; any visible char after `>` is treated as typed.
- **Codex** idle placeholder is dim and classifies as `ghost` (correct: it is not input).
- An **unknown** runtime always yields `unknown` — add its signature to
  `runtime_signatures.PROMPT_SIGNATURES` (with a real capture) before it can be classified.
