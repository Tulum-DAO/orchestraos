/**
 * streamMarkdown.ts — a reply still being written, as the reveal draws it (components/kit/StreamMarkdown.tsx;
 * SMOOTH_STREAM_SPEC.md). Pure, so tested without a browser.
 *
 *  - revealSafeLength: how much of the text can be drawn now without anything drawn changing later. A word still
 *    being written waits for the space after it; a line that could still turn into a list, a heading, a quote or a
 *    fence waits until it is sure (and until it has a whole word after its mark, so no block is ever drawn empty); an
 *    inline mark that could still close (`code`, **bold**, *italic*, a [link](…)) waits for its close; a fenced code
 *    block is drawn a whole line at a time once its first line is complete; a table waits until it is complete (a line
 *    that is not part of it follows) and then appears as one piece; a line with a pipe that could still be a table's
 *    head waits for the line after it.
 *  - planBlocks: the thread kit's Markdown blocks (lib/threadKit/markdown.ts) as units the reveal releases in order,
 *    numbered from `start`: each word (with its trailing space), each inline code span, link and card chip, each code
 *    line, a folded code block's "more lines" row, and a whole table. A list item's marker and a quote's bar belong to
 *    their first word; a code block's frame to its first line.
 *
 * Finished blocks are cut where lib/threadKit/paragraphs.ts completeLength says the complete paragraphs end, planned
 * once and never again; only the block still growing is read again as words arrive.
 */
import { isTableStart, parseBlocks, parseInline, unclosedInline, type Block } from './markdown';
import { hasRefs, isCardId, splitRefs } from '../cardRefs';
import { splitWords } from '../reveal';
import { completeLength } from './paragraphs';

/** A fenced block longer than this is folded (components/kit/Markdown.tsx CodeBlock): while it streams too. */
export const CODE_FOLD_LINES = 14;

const LIST_RE = /^\s*([-*+]|\d+[.)])\s+/;

export function revealSafeLength(text: string, done = false): number {
  if (done) return text.length;
  const lines = text.split('\n');
  const n = lines.length - 1;                       // complete lines; lines[n] is still being written ('' after a \n)
  const starts: number[] = [];
  for (let i = 0, o = 0; i <= n; i++) { starts.push(o); o += lines[i].length + 1; }
  const complete = lines.slice(0, n);
  // Fences and tables among the complete lines, read as parseBlocks reads them.
  let fence = -1, fenceLines = 0, table = -1;
  for (let i = 0; i < n; i++) {
    const l = lines[i];
    if (fence >= 0) { if (/^```/.test(l)) fence = -1; else fenceLines++; continue; }
    if (/^```/.test(l)) { fence = i; fenceLines = 0; table = -1; continue; }
    if (table >= 0) { if (l.trim() && l.includes('|')) continue; table = -1; }
    if (isTableStart(complete, i)) { table = i; i++; }
  }
  if (fence >= 0) return fenceLines > 0 ? starts[n] : starts[fence];   // code: whole lines, once there is one
  if (table >= 0) return starts[table];                                  // a table: whole, once it is complete
  const last = lines[n];
  // A complete line with a pipe just before the one being written could be a table's head.
  if (n > 0) {
    const prev = lines[n - 1];
    if (prev.includes('|') && prev.trim() && /^[\s|:-]*$/.test(last)) return starts[n - 1];
  }
  let end = text.length;
  if (!/\s$/.test(text)) {
    const ws = Math.max(text.lastIndexOf(' '), text.lastIndexOf('\n'), text.lastIndexOf('\t'));
    end = ws + 1;
  }
  if (last) {
    const lineStart = starts[n];
    const hold = () => { end = Math.min(end, lineStart); };
    if (/^`{1,2}$/.test(last) || /^```/.test(last)) hold();                 // could be a fence
    else if (/^#{1,4}$/.test(last)) hold();                                 // could be a heading
    else if (/^\s*([-*+]|\d{1,9}[.)]?)$/.test(last)) hold();               // could be a list item
    else if (last.includes('|')) hold();                                    // could be a table
    else {
      const mark = last.match(LIST_RE) ?? last.match(/^#{1,4}\s+/) ?? last.match(/^\s*>\s*/);
      if (mark && end <= lineStart + mark[0].length) hold();               // a mark with no whole word after it yet
    }
  }
  if (end <= 0) return 0;
  // An inline mark that could still close: from the start of its word on, wait.
  const cut = text.slice(0, end);
  const from = completeLength(cut);              // where the block still growing starts
  const scope = LIST_RE.test(cut.slice(from)) ? cut.lastIndexOf('\n', end - 2) + 1 : from;   // a list: its last item
  const seg = cut.slice(Math.max(from, scope));
  const open = unclosedInline(seg);
  if (open >= 0) {
    const at = Math.max(from, scope) + open;
    const ws = Math.max(cut.lastIndexOf(' ', at - 1), cut.lastIndexOf('\n', at - 1), cut.lastIndexOf('\t', at - 1));
    end = Math.min(end, ws + 1);
  }
  return end;
}


/** Whether a text being written has a word to draw yet (a reply's block or bubble is never drawn empty). */
export function revealReady(text: string, done = false): boolean {
  const t = String(text ?? '').replace(/^\s+/, '');
  return t.slice(0, revealSafeLength(t, done)).trim() !== '';
}

// ---- Units --------------------------------------------------------------------------------------------------------

/** A word with its trailing space (a unit), a card chip (a unit), or space between them (none). */
export type Word = { w: string; u: number } | { ref: string; u: number } | { sp: string };
export type Seg =
  | { t: 'text' | 'bold' | 'italic'; words: Word[] }
  | { t: 'code'; text: string; u: number }
  | { t: 'link'; text: string; href: string; u: number };
type TableBlock = Extract<Block, { t: 'table' }>;
export type BlockPlan =
  | { t: 'para'; segs: Seg[] }
  | { t: 'heading'; level: number; segs: Seg[] }
  | { t: 'list'; ordered: boolean; start: number; items: { segs: Seg[]; u: number | null }[] }
  | { t: 'quote'; segs: Seg[]; u: number | null }
  | { t: 'code'; code: string; lang: string; lines: number[]; fold: number | null }
  | { t: 'table'; block: TableBlock; u: number };

class Counter {
  next: number;
  constructor(next: number) { this.next = next; }
  take() { return this.next++; }
}

function wordsOf(text: string, c: Counter): Word[] {
  const pieces = hasRefs(text) ? splitRefs(text) : [{ t: 'text' as const, text }];
  const out: Word[] = [];
  for (const p of pieces) {
    if (p.t === 'ref') { out.push({ ref: p.ref.id, u: c.take() }); continue; }
    for (const w of splitWords(p.text)) out.push(/^\s+$/.test(w) ? { sp: w } : { w, u: c.take() });
  }
  return out;
}

function segsOf(text: string, c: Counter): Seg[] {
  return parseInline(text).map((p): Seg => {
    switch (p.t) {
      case 'code': return isCardId(p.text) ? { t: 'text', words: wordsOf(p.text.trim(), c) } : { t: 'code', text: p.text, u: c.take() };
      case 'link': return { t: 'link', text: p.text, href: p.href, u: c.take() };
      case 'bold': case 'italic': return { t: p.t, words: wordsOf(p.text, c) };
      default: return { t: 'text', words: wordsOf(p.text, c) };
    }
  });
}

/** The first unit of these pieces, or null when they have none. */
function firstUnit(segs: Seg[]): number | null {
  for (const s of segs) {
    if ('u' in s) return s.u;
    for (const w of s.words) if ('u' in w) return w.u;
  }
  return null;
}

/** These blocks as units, numbered from `start`; `next` is the number after the last. */
export function planBlocks(blocks: Block[], start = 0): { plans: BlockPlan[]; next: number } {
  const c = new Counter(start);
  const plans = blocks.map((b): BlockPlan => {
    switch (b.t) {
      case 'code': {
        const n = b.code.replace(/\n$/, '').split('\n').length;
        const shown = Math.min(n, CODE_FOLD_LINES);
        const lines = Array.from({ length: shown }, () => c.take());
        return { t: 'code', code: b.code, lang: b.lang, lines, fold: n > CODE_FOLD_LINES ? c.take() : null };
      }
      case 'heading': return { t: 'heading', level: b.level, segs: segsOf(b.text, c) };
      case 'list': return { t: 'list', ordered: b.ordered, start: b.start, items: b.items.map((it) => { const segs = segsOf(it, c); return { segs, u: firstUnit(segs) }; }) };
      case 'quote': { const segs = segsOf(b.text, c); return { t: 'quote', segs, u: firstUnit(segs) }; }
      case 'table': return { t: 'table', block: b, u: c.take() };
      default: return { t: 'para', segs: segsOf(b.text, c) };
    }
  });
  return { plans, next: c.next };
}

/** A text's blocks as units (parseBlocks, then planBlocks). */
export function planText(text: string, start = 0): { plans: BlockPlan[]; next: number } {
  return text.trim() ? planBlocks(parseBlocks(text), start) : { plans: [], next: start };
}

// ---- Finished and growing -------------------------------------------------------------------------------------------

/** One stretch of finished blocks, planned once. */
export interface PlannedChunk { from: number; to: number; plans: BlockPlan[]; units: number; firstUnit: number }

/**
 * A streamed reply's plan, kept across renders: the finished stretches (`chunks`, never planned again) and the
 * block still growing (planned on every call). `update` takes the whole text so far; it returns false when the text
 * is not an extension of what was planned (the caller starts over).
 */
export class StreamPlan {
  text = '';
  /** How much of `text` is drawn (revealSafeLength). */
  safe = 0;
  chunks: PlannedChunk[] = [];
  tail: BlockPlan[] = [];
  units = 0;
  private finished = 0;   // where the finished stretches end
  private finishedUnits = 0;

  update(text: string, done: boolean): boolean {
    // (the finished message is the same words trimmed: a trailing space or line break drawn live may be gone)
    if (!text.startsWith(this.text.slice(0, this.safe).trimEnd())) return false;
    this.text = text;
    // Never less than was drawn before: a word once drawn stays (a line that only later shows it is a table's head,
    // with no pipe at its start, is the one case that changes shape).
    const safe = Math.min(text.length, Math.max(revealSafeLength(text, done), this.safe, this.finished));
    this.safe = safe;
    const drawn = text.slice(0, safe);
    const complete = done ? safe : Math.max(this.finished, completeLength(drawn));
    if (complete > this.finished) {
      const { plans, next } = planText(text.slice(this.finished, complete), this.finishedUnits);
      if (plans.length) this.chunks.push({ from: this.finished, to: complete, plans, units: next - this.finishedUnits, firstUnit: this.finishedUnits });
      this.finished = complete;
      this.finishedUnits = next;
    }
    const tail = planText(drawn.slice(this.finished), this.finishedUnits);
    this.tail = tail.plans;
    this.units = tail.next;
    return true;
  }

  /** Every block, finished first, in order. */
  blocks(): BlockPlan[] {
    return [...this.chunks.flatMap((c) => c.plans), ...this.tail];
  }
}
