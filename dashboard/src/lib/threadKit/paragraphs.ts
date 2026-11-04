/**
 * paragraphs.ts — where the complete paragraphs of a reply still being written end. The streamed reply's reveal
 * (lib/threadKit/streamMarkdown.ts) plans what is before that point once, as finished blocks, and reads only the
 * rest again as words arrive. (Bubbles used to show only the complete paragraphs; since 2026-11-02 every skin shows
 * the words as they arrive.)
 *
 * A paragraph is complete at a blank line; at the start of the next block (a heading, a list, a table or a code
 * fence ends the plain text before it); a list or a table when a line that is not part of it follows; a code fence
 * only when it is closed (half a fence or half a table is never shown); a heading at the end of its line; and
 * everything when the turn has ended. The last line without its line break is never complete: its last word may be
 * half written. Pure, so tested without a browser.
 */
import type { MessagePart } from './thread';

const FENCE_RE = /^\s*(```|~~~)/;
const LIST_RE = /^\s*([-*+]|\d+[.)])\s+/;
const HEADING_RE = /^#{1,6}\s/;
const isTableLine = (l: string) => /^\s*\|/.test(l) || (l.includes('|') && /\|\s*$/.test(l.trim()));

type Block = 'none' | 'para' | 'list' | 'table' | 'fence';

/** How much of `text` is complete paragraphs: the length of that prefix. `done`: the turn has ended, so all of it. */
export function completeLength(text: string, done = false): number {
  if (done) return text.length;
  let safe = 0;
  let block: Block = 'none';
  let start = 0;
  while (start < text.length) {
    const nl = text.indexOf('\n', start);
    if (nl < 0) break;                                  // the last line is still being written
    const line = text.slice(start, nl);
    const end = nl + 1;
    if (block === 'fence') {
      if (FENCE_RE.test(line)) { safe = end; block = 'none'; }
    } else if (FENCE_RE.test(line)) {
      safe = start; block = 'fence';
    } else if (!line.trim()) {
      safe = end; block = 'none';
    } else if (HEADING_RE.test(line)) {
      safe = end; block = 'none';
    } else if (isTableLine(line)) {
      if (block !== 'table') { safe = start; block = 'table'; }
    } else if (LIST_RE.test(line)) {
      if (block !== 'list') { safe = start; block = 'list'; }
    } else if (block === 'list' && /^\s+\S/.test(line)) {
      // a list item's continuation line
    } else if (block === 'list' || block === 'table') {
      safe = start; block = 'para';
    } else {
      block = 'para';
    }
    start = end;
  }
  return safe;
}

/** The complete paragraphs of `text`, and whether more is still being written behind them. */
export function completeText(text: string, done = false): { text: string; pending: boolean } {
  const n = completeLength(text, done);
  return { text: text.slice(0, n).replace(/\s+$/, ''), pending: text.slice(n).trim().length > 0 };
}

/** A reply with tool runs, as Bubbles shows it while it is written: text before a tool run is finished (the run
 *  followed it); the last text part shows its complete paragraphs only. */
export function completeParts(parts: MessagePart[], done = false): { parts: MessagePart[]; pending: boolean } {
  const out: MessagePart[] = [];
  let pending = false;
  parts.forEach((p, i) => {
    if (p.kind === 'tools' || i < parts.length - 1) { out.push(p); return; }
    const c = completeText(p.text, done);
    pending = c.pending;
    if (c.text) out.push({ kind: 'text', text: c.text });
  });
  return { parts: out, pending };
}
