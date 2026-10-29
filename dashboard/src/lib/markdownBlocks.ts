/**
 * markdownBlocks.ts — the Markdown every agent's messages are written in, read into blocks and inline pieces that
 * components/chat/Markdown.tsx draws. One reader for Arturo's page and every agent chat (Modern and Bubbles are two
 * looks of the same message). Dependency-free on purpose: no CDN, no markdown library in the bundle.
 *
 * Blocks: fenced code, headings, bullet and numbered lists, quotes, GitHub-style tables, paragraphs.
 * Inline: `code`, **bold**, *italic*, [links](https://…) and bare https:// addresses. A link is kept only for
 * http, https and mailto; anything else (javascript:, data:, a relative path) stays plain text.
 */

export type Inline =
  | { t: 'text'; text: string }
  | { t: 'code'; text: string }
  | { t: 'bold'; text: string }
  | { t: 'italic'; text: string }
  | { t: 'link'; text: string; href: string };

export type Block =
  | { t: 'code'; code: string; lang: string }
  | { t: 'heading'; level: number; text: string }
  | { t: 'list'; ordered: boolean; start: number; items: string[] }
  | { t: 'quote'; text: string }
  | { t: 'table'; head: string[]; align: ('left' | 'center' | 'right' | null)[]; rows: string[][] }
  | { t: 'para'; text: string };

const LIST_RE = /^\s*([-*+]|\d+[.)])\s+/;
const TABLE_SEP_RE = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

/** A safe link target, or null: only http(s) and mailto are followed. */
export function safeHref(raw: string): string | null {
  const href = raw.trim();
  return /^(https?:\/\/[^\s<>"]+|mailto:[^\s<>"]+)$/i.test(href) ? href : null;
}

function cells(line: string): string[] {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1);
  // a pipe written as \| stays part of the cell
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, '|'));
}

function isTableStart(lines: string[], i: number): boolean {
  return i + 1 < lines.length && lines[i].includes('|') && TABLE_SEP_RE.test(lines[i + 1])
    && cells(lines[i]).length === cells(lines[i + 1]).length;
}

function structural(lines: string[], i: number): boolean {
  const l = lines[i];
  return /^```/.test(l) || /^#{1,4}\s/.test(l) || LIST_RE.test(l) || /^\s*>/.test(l) || isTableStart(lines, i);
}

export function parseBlocks(text: string): Block[] {
  const lines = String(text ?? '').replace(/\r\n?/g, '\n').split('\n');
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^```\s*([\w+-]*)/);
    if (fence) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;                                                  // the closing fence (or the end, for an unclosed one)
      out.push({ t: 'code', code: buf.join('\n'), lang: fence[1] || '' });
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) { out.push({ t: 'heading', level: h[1].length, text: h[2].trim() }); i++; continue; }
    if (isTableStart(lines, i)) {
      const head = cells(lines[i]);
      const align = cells(lines[i + 1]).map((c) => (c.startsWith(':') && c.endsWith(':') ? 'center'
        : c.endsWith(':') ? 'right' : c.startsWith(':') ? 'left' : null));
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes('|')) {
        const r = cells(lines[i++]);
        rows.push(head.map((_, k) => r[k] ?? ''));          // ragged rows are padded or cut to the header
      }
      out.push({ t: 'table', head, align, rows });
      continue;
    }
    if (LIST_RE.test(line)) {
      const first = line.match(LIST_RE)![1];
      const ordered = /\d/.test(first);
      const items: string[] = [];
      while (i < lines.length && LIST_RE.test(lines[i]) && /\d/.test(lines[i].match(LIST_RE)![1]) === ordered) {
        items.push(lines[i].replace(LIST_RE, ''));
        i++;
      }
      out.push({ t: 'list', ordered, start: ordered ? parseInt(first, 10) || 1 : 1, items });
      continue;
    }
    if (/^\s*>/.test(line)) {
      const buf: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ''));
      out.push({ t: 'quote', text: buf.join('\n') });
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const para: string[] = [line];
    i++;
    while (i < lines.length && lines[i].trim() && !structural(lines, i)) para.push(lines[i++]);
    out.push({ t: 'para', text: para.join('\n') });
  }
  return out;
}

// One pass over the text for everything inline; the earliest match wins.
const INLINE_RE = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]\n]+\]\([^)\s]+\))|(\bhttps?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])|(\*[^*\s][^*]*\*)/g;

export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  const s = String(text ?? '');
  let last = 0;
  let m: RegExpExecArray | null;
  INLINE_RE.lastIndex = 0;
  const push = (p: Inline) => {
    const prev = out[out.length - 1];
    if (p.t === 'text' && prev?.t === 'text') prev.text += p.text;
    else out.push(p);
  };
  while ((m = INLINE_RE.exec(s)) !== null) {
    if (m.index > last) push({ t: 'text', text: s.slice(last, m.index) });
    const tok = m[0];
    if (m[1]) push({ t: 'code', text: tok.slice(1, -1) });
    else if (m[2]) push({ t: 'bold', text: tok.slice(2, -2) });
    else if (m[3]) {
      const lm = tok.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/)!;
      const href = safeHref(lm[2]);
      push(href ? { t: 'link', text: lm[1], href } : { t: 'text', text: lm[1] });
    } else if (m[4]) {
      const href = safeHref(tok);
      push(href ? { t: 'link', text: tok, href } : { t: 'text', text: tok });
    } else push({ t: 'italic', text: tok.slice(1, -1) });
    last = m.index + tok.length;
  }
  if (last < s.length) push({ t: 'text', text: s.slice(last) });
  return out;
}

/** Leading attachment markers a composer puts on a message ("[IMAGE: /…/state/uploads/x.png] the words"), split off.
 *  An image saved in the uploads folder is shown from /uploads/<file>; anything else stays a file chip. */
export interface Attachment { kind: 'image' | 'file'; name: string; url: string | null }

export function splitAttachments(text: string): { attachments: Attachment[]; text: string } {
  const attachments: Attachment[] = [];
  let rest = String(text ?? '');
  const re = /^\s*\[(IMAGE|FILE): ([^\]\n]+)\]/;
  let m: RegExpMatchArray | null;
  while ((m = rest.match(re))) {
    const path = m[2].trim();
    const name = path.split('/').pop() || path;
    const inUploads = /\/state\/uploads\/[^/]+$/.test(path) && /^[\w.-]+$/.test(name);
    attachments.push({ kind: m[1] === 'IMAGE' ? 'image' : 'file', name, url: inUploads ? `/uploads/${name}` : null });
    rest = rest.slice(m[0].length);
  }
  return { attachments, text: attachments.length ? rest.trimStart() : rest };
}
