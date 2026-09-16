/**
 * Pasted-text marker grammar (spec docs/superpowers/specs/2026-08-11-pasted-text-grammar.md).
 *
 * WRITE end (composer): large pastes are serialized INLINE at their original
 * character position, fenced, so the agent reads prose+paste in order with no
 * indirection. READ end (transcript): fenced spans render as grouped "Pasted
 * text #n" cards at their position in the message flow.
 *
 * Grammar (FROZEN — extends the [attached:]/[voice-call:] marker family):
 *   [pasted #<n> begin <lines> lines <chars> chars]
 *   ...verbatim content...
 *   [pasted #<n> end]
 *
 * Threshold: a paste ≥1000 chars OR ≥10 lines → fenced; below → plain inline
 * text (indistinguishable from typing). One constant, both surfaces.
 */

export const PASTE_MIN_CHARS = 1000;
export const PASTE_MIN_LINES = 10;

export function isLargePaste(s: string): boolean {
  return s.length >= PASTE_MIN_CHARS || s.split('\n').length >= PASTE_MIN_LINES;
}

const ZWSP = '\u200b';
const FENCE_LINE_RE = /^\[pasted #\d+ (begin|end)/;

/** Escape content lines that would collide with a fence line (prefix ZWSP). */
export function escapePasteContent(content: string): string {
  return content
    .split('\n')
    .map((ln) => (FENCE_LINE_RE.test(ln) ? ZWSP + ln : ln))
    .join('\n');
}

/** Build one fenced block. n is the 1-based per-message counter. */
export function fencePaste(n: number, content: string): string {
  const lines = content.split('\n').length;
  const chars = content.length;
  return `[pasted #${n} begin ${lines} lines ${chars} chars]\n${escapePasteContent(content)}\n[pasted #${n} end]`;
}

// ── READ end ────────────────────────────────────────────────────────────────

export type MessageSegment =
  | { kind: 'prose'; text: string }
  | { kind: 'paste'; n: number; lines: number; chars: number; content: string };

const BEGIN_RE = /^\[pasted #(\d+) begin (\d+) lines (\d+) chars\][ \t]*$/;
const END_RE = /^\[pasted #(\d+) end\][ \t]*$/;

/** True if a message contains at least one well-formed paste fence. Fast reject
 *  keeps normal messages on the untouched single-bubble path. */
export function hasPasteMarkers(text: string): boolean {
  return /\[pasted #\d+ begin \d+ lines \d+ chars\]/.test(text);
}

/**
 * Split a message into ordered prose/paste segments. Prose between/around
 * fences is preserved verbatim (position-accurate). A begin without a matching
 * end (truncated transcript) falls back to treating the rest as paste content.
 * ZWSP-escaped content lines are unescaped on the way out.
 */
export function parseMessageSegments(text: string): MessageSegment[] {
  if (!hasPasteMarkers(text)) return [{ kind: 'prose', text }];
  const lines = text.split('\n');
  const segs: MessageSegment[] = [];
  let prose: string[] = [];
  const flushProse = () => {
    if (prose.length) {
      const t = prose.join('\n');
      // keep prose even if only whitespace-with-content; drop pure empties
      if (t.trim()) segs.push({ kind: 'prose', text: t.replace(/^\n+|\n+$/g, '') });
      prose = [];
    }
  };
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(BEGIN_RE);
    if (!m) { prose.push(lines[i]); continue; }
    // collect until matching end
    const n = Number(m[1]);
    const declLines = Number(m[2]);
    const declChars = Number(m[3]);
    const body: string[] = [];
    let j = i + 1;
    let closed = false;
    for (; j < lines.length; j++) {
      if (END_RE.test(lines[j]) && Number(lines[j].match(END_RE)![1]) === n) { closed = true; break; }
      // unescape a ZWSP-guarded fence line
      body.push(lines[j].startsWith(ZWSP) ? lines[j].slice(1) : lines[j]);
    }
    flushProse();
    const content = body.join('\n');
    segs.push({ kind: 'paste', n, lines: declLines || body.length, chars: declChars || content.length, content });
    i = closed ? j : lines.length; // consume through the end fence (or to EOF)
  }
  flushProse();
  return segs.length ? segs : [{ kind: 'prose', text }];
}
