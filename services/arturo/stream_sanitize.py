"""Phase 2 §2.3 — first-character classification and the streaming sanitisers.

Voice sanitises a finished reply in one batch:

    strip_leading_tool_reasoning(strip_thought_block(strip_tool_code(text)))

A streamed turn has to produce the SAME bytes while the model is still typing, or streaming
quietly becomes a second answer that drifts from the one the transcript keeps. So nothing
here reimplements a rule: the batch functions in voice_guards are the reference, and this
module only decides WHEN a piece of output can no longer change.

Two devices:

* `PassClassifier` — §2.3's first-character rule. `{`, a code fence or `<` means the pass is
  structure (a tool call or native markup): HOLD it and parse it whole. Anything else is
  prose, streamed through the sanitiser below. Whitespace alone decides nothing, and a lone
  backtick is undecided until it is clear whether a fence is opening.

* `StreamingSanitizer` — emits the longest prefix that is provably final. What forces a hold:
    - an OPEN code fence, wherever it opens (the mid-text case): an unterminated fence
      sanitises differently from the complete one it is about to become;
    - a partial line, because a bare `tool_code` / `print(default_api` line is only
      recognisable once the line ends;
    - trailing whitespace, because the batch collapses blank runs and strips both ends;
    - the leading region, because a thought block keeps only its LAST paragraph, and leading
      sentences containing a backticked token are stripped until one arrives without any.
      That last rule is why the first sentence of a reply streams as a unit: "Try this:"
      reads as prose right up until a code fence lands in the same sentence and the whole
      thing is dropped.
"""
import re

from . import voice_guards as _vg

HOLD = "hold"
PROSE = "prose"

_FENCE = "```"
_SENTENCE_END_RE = re.compile(r"[.!?]")
# Could this partial line still turn into a bare tool line? Matches a complete opener, and
# any PREFIX of one, because the line is not finished yet.
_BARE_TOOL_OPENERS = ("tool_code", "print(default_api", "default_api.")


def _could_become_tool_line(partial: str) -> bool:
    head = partial.lstrip().lower()
    squashed = head.replace(" ", "").replace("\t", "")
    for opener in _BARE_TOOL_OPENERS:
        if squashed.startswith(opener) or opener.startswith(squashed):
            return True
    return False


def batch_sanitize(text):
    """THE reference: exactly what voice journals today, in the same order."""
    return _vg.strip_leading_tool_reasoning(
        _vg.strip_thought_block(
            _vg.strip_tool_code(text)))


class PassClassifier:
    """§2.3 first-character classification for ONE pass. Decides once, then stays decided."""

    def __init__(self):
        self.verdict = None
        self.buffer = ""
        self.emitted = ""

    def feed(self, chunk):
        self.buffer += chunk
        if self.verdict is not None:
            return self.verdict
        stripped = self.buffer.lstrip()
        if not stripped:
            return None                                  # whitespace only — undecided
        if stripped[0] == "`" and not stripped.startswith(_FENCE):
            if _FENCE.startswith(stripped[:3]):
                return None                              # may still become a fence
        if stripped[0] in "{<" or stripped.startswith(_FENCE):
            self.verdict = HOLD
        else:
            self.verdict = PROSE
        return self.verdict


class StreamDivergence(RuntimeError):
    def __init__(self, sent, final):
        super().__init__("streamed prefix is not a prefix of the batch answer")
        self.sent = sent
        self.final = final


class StreamingSanitizer:
    """Incremental `batch_sanitize`: the feeds plus finish() equal the batch answer."""

    def __init__(self):
        self._raw = ""
        self._sent = ""
        self._done = False

    def _leading_region_resolved(self, raw):
        """(resolved, index) — is it settled where the leading tool-reasoning ends?"""
        text = _vg.strip_tool_code(raw)
        rest, idx = text, 0
        while True:
            m = _vg._TICKED_SENTENCE_RE.match(rest)
            if not m:
                break
            rest = rest[m.end():].lstrip()
            idx += m.end()
        end = _SENTENCE_END_RE.search(rest)
        if not end:
            return False, idx          # sentence still open: a backtick may yet arrive
        if "`" in rest[:end.end()]:
            return False, idx          # complete but ticked — the loop above will eat it
        return True, idx

    def _stable_len(self):
        """Length of the raw prefix whose sanitised form cannot change with more input."""
        raw = self._raw
        # A thought block keeps only its FINAL paragraph, so once the text opens with the
        # marker (voice_guards' own test, not a copy of it) nothing is settled until the end.
        if _vg._THOUGHT_MARKER_RE.match(_vg.strip_tool_code(raw) or ""):
            return 0
        resolved, _head_end = self._leading_region_resolved(raw)
        if not resolved:
            return 0
        # Past the leading region the only rules left are strip_tool_code's, so text may leave
        # WORD BY WORD rather than a sentence at a time — streaming that waits for a full stop
        # is barely streaming (operator, 2026-09-29). What still has to be held:
        #   - anything inside an open fence, wherever it opened;
        #   - a partial line that could still become a bare `tool_code` / `print(default_api`
        #     line, since that is only recognisable once the line ends.
        cut = len(raw)
        last_nl = raw.rfind("\n")
        line_start = last_nl + 1 if last_nl >= 0 else 0
        if _could_become_tool_line(raw[line_start:]):
            cut = line_start
        # A trailing run of backticks may be a fence about to open — "``" is not yet "```",
        # and emitting it means the fence's first characters have already escaped.
        while cut > 0 and raw[cut - 1] == "`":
            cut -= 1
        while cut > 0 and raw[:cut].count(_FENCE) % 2 == 1:
            open_at = raw.rindex(_FENCE, 0, cut)
            fence_line = raw.rfind("\n", 0, open_at)
            cut = fence_line + 1 if fence_line >= 0 else 0
        settled = raw[:cut]
        return len(settled.rstrip()) if settled.strip() else 0

    def feed(self, chunk):
        if self._done:
            raise RuntimeError("feed() after finish()")
        self._raw += chunk
        stable = self._stable_len()
        if stable <= 0:
            return ""
        settled = batch_sanitize(self._raw[:stable])
        if not settled.startswith(self._sent):
            # A later rule rewrote what was already sent. Never emit a correction — hold,
            # and let finish() be the one place that can report a divergence.
            return ""
        out = settled[len(self._sent):]
        self._sent = settled
        return out

    def finish(self):
        self._done = True
        final = batch_sanitize(self._raw)
        if not final.startswith(self._sent):
            raise StreamDivergence(sent=self._sent, final=final)
        out = final[len(self._sent):]
        self._sent = final
        return out
