/**
 * What any agent's call knows at start: the agent's chat as the operator sees it plus every phrase of every call that
 * agent took, in time order, every line stamped [ISO-UTC  Shaw-local]. Newest verbatim up to a token budget; what does
 * not fit is one summary line per call or chat stretch (Shaw 2026-11-25: "each new call has everything above it,
 * including previous calls, and timestamps of every message sent and every phrase spoken" — for ANY agent).
 *
 * The TS twin of services/arturo/thread_context.py. The two produce the SAME BYTES for the same inputs; a shared
 * fixture (contract/call-context-cases.json) pins both suites. Change one, change both.
 *
 * Same bytes for secret-free input. Secrets: each twin redacts every piece BEFORE clipping it (a secret cut at the
 * clip boundary would otherwise ship as a prefix); the route passes the full redactor plus this install's token.
 *
 * Python counts code points, so lengths and slices here do too (cp*), never UTF-16 units. Whitespace is Python's
 * str.isspace() set (WS below), not JS \s (which adds U+FEFF and lacks U+0085 and \x1c-\x1f). A time is an epoch
 * number or a full ISO date-time with a zone (never a date alone, never a boolean), kept to the millisecond.
 */
import fs from 'fs';
import path from 'path';

export const DEFAULT_BUDGET_TOKENS = 6000;
export const MIN_BUDGET_TOKENS = 500;
export const MAX_BUDGET_TOKENS = 20000;
const LINE_MAX_CHARS = 1200;
const SUMMARY_SHARE = 0.2;
const SUMMARY_ITEM_CHARS = 200;
const CALL_FILES_MAX = 40;
const CALL_MAX_AGE_S = 14 * 86400;
const SECRET_RE = /(sk-)[A-Za-z0-9_-]{6,}|((?:KEY|TOKEN|SECRET|PASSWORD)[A-Z_]*=)\S+/gi;
const SPOKEN_ROLES_SKIP = new Set(['tool', 'system', 'tool_result', 'function']);

export interface Line { at: number; who: string; text: string; channel: 'chat' | 'call'; call_id: string }

const cpLen = (s: string) => Array.from(s).length;
const cpSlice = (s: string, a: number, b?: number) => Array.from(s).slice(a, b).join('');
const WS = '\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000';
const WS_RUN = new RegExp(`[${WS}]+`, 'g');
const WS_EDGES = new RegExp(`^[${WS}]+|[${WS}]+$`, 'g');
const squash = (s: string) => s.split(WS_RUN).filter(Boolean).join(' ');
const strip = (s: string) => s.replace(WS_EDGES, '');
const NUM_RE = /^-?\d+(\.\d+)?$/;
const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})$/;
const toMs = (x: number) => Math.floor(x * 1000) / 1000;

export const estimateTokens = (text: string) => Math.ceil(cpLen(text || '') / 4);

export function clampBudget(v: unknown): number {
  const n = parseInt(String(v ?? ''), 10);
  if (!Number.isFinite(n)) return DEFAULT_BUDGET_TOKENS;
  return Math.max(MIN_BUDGET_TOKENS, Math.min(n, MAX_BUDGET_TOKENS));
}

export function epoch(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? toMs(v) : null;
  if (typeof v !== 'string') return null;
  const t = strip(v);
  if (NUM_RE.test(t)) return toMs(Number(t));
  if (!ISO_RE.test(t)) return null;
  const ms = Date.parse(t);
  return Number.isNaN(ms) ? null : toMs(ms / 1000);
}

export const redact = (text: string) =>
  (text || '').replace(SECRET_RE, (_m, sk, kv) => (sk ? `${sk}[redacted]` : `${kv}[redacted]`));

export type Redactor = (s: string) => string;

function clip(text: string, n: number, red: Redactor): string {
  const t = squash(red(String(text || '')));
  return cpLen(t) <= n ? t : cpSlice(t, 0, n - 1) + '…';
}

// --- sources --------------------------------------------------------------------------------------------------------

export function chatLines(items: any[], agentLabel: string, operatorLabel = 'Operator', since: number | null = null): Line[] {
  const out: Line[] = [];
  for (const it of items || []) {
    if (!it || typeof it !== 'object' || it.kind !== 'text' || it.audience !== 'user' || it.machine) continue;
    if (typeof it.text !== 'string') continue;
    const text = strip(it.text);
    const at = epoch(it.ts);
    if (!text || at === null || (since !== null && at <= since)) continue;
    out.push({ at, who: it.role === 'user' ? operatorLabel : agentLabel, text, channel: 'chat', call_id: '' });
  }
  return out;
}

export interface Journal { file: string; doc: any }

/** The newest journals by mtime (then path, descending, as the Python sorts its tuples), parsed; unreadable skipped. */
export function readJournals(callsDir: string, now: number): Journal[] {
  let names: string[] = [];
  try { names = fs.readdirSync(callsDir).filter((n) => /^vc_.*\.json$/.test(n)); } catch { return []; }
  const files: [number, string][] = [];
  for (const n of names) {
    const f = path.join(callsDir, n);
    try {
      const mt = fs.statSync(f).mtimeMs / 1000;
      if (now - mt <= CALL_MAX_AGE_S) files.push([mt, f]);
    } catch { /* gone */ }
  }
  files.sort((a, b) => (b[0] - a[0]) || (a[1] < b[1] ? 1 : a[1] > b[1] ? -1 : 0));
  const out: Journal[] = [];
  for (const [, f] of files.slice(0, CALL_FILES_MAX)) {
    try { out.push({ file: f, doc: JSON.parse(fs.readFileSync(f, 'utf-8')) }); } catch { /* mid-write */ }
  }
  return out;
}

export function callLines(journals: Journal[], owner: string | null, exclude: string | null = null,
  assistantLabel: string | null = null, operatorLabel = 'Operator'): Line[] {
  const label = assistantLabel || owner || 'Arturo';
  const out: Line[] = [];
  for (const { file, doc } of journals) {
    if (!doc || typeof doc !== 'object' || Array.isArray(doc)) continue;
    const cid = String(doc.call_id || path.basename(file).slice(0, -5));
    if (exclude && cid === exclude) continue;
    if ((doc.agent || null) !== (owner || null)) continue;
    const start = epoch(doc.started_at);
    for (const t of Array.isArray(doc.turns) ? doc.turns : []) {
      if (!t || typeof t !== 'object') continue;
      const role = String(t.role || '');
      if (typeof t.text !== 'string') continue;
      const text = strip(t.text);
      if (!text || SPOKEN_ROLES_SKIP.has(role)) continue;
      const at = epoch(t.ts) ?? start;
      if (at === null) continue;
      out.push({ at, who: role === 'user' ? operatorLabel : label, text, channel: 'call', call_id: cid });
    }
  }
  return out;
}

// --- rendering ------------------------------------------------------------------------------------------------------

function localHM(at: number, tz: string): string {
  const d = new Date(at * 1000);
  try {
    const parts = new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
      .formatToParts(d);
    const h = parts.find((p) => p.type === 'hour')?.value ?? '00';
    const m = parts.find((p) => p.type === 'minute')?.value ?? '00';
    return `${h}:${m}`;
  } catch {
    return d.toISOString().slice(11, 16);              // a bad zone name shows UTC, never fails a call
  }
}

export function stamp(at: number, tz: string, tzLabel: string): string {
  const utc = new Date(Math.floor(at) * 1000).toISOString().slice(0, 19) + 'Z';
  return `[${utc} ${localHM(Math.floor(at), tz)} ${tzLabel}]`;
}

const where = (l: Line) => (l.channel === 'call' ? `call ${l.call_id}` : 'chat');

export const formatLine = (l: Line, tz: string, tzLabel: string, red: Redactor = redact) =>
  `${stamp(l.at, tz, tzLabel)} ${where(l)} ${l.who}: ${clip(l.text, LINE_MAX_CHARS, red)}`;

function summaries(dropped: Line[], tz: string, tzLabel: string, red: Redactor): string[] {
  const groups: [string, Line[]][] = [];
  for (const l of dropped) {
    const key = l.channel === 'call' ? l.call_id : 'chat';
    if (groups.length && groups[groups.length - 1][0] === key) groups[groups.length - 1][1].push(l);
    else groups.push([key, [l]]);
  }
  return groups.map(([key, ls]) => {
    const first = ls[0];
    const gist = clip(ls.slice(-2).map((l) => `${l.who}: ${l.text}`).join(' / '), SUMMARY_ITEM_CHARS, red);
    return key === 'chat'
      ? `- ${stamp(first.at, tz, tzLabel)} chat, ${ls.length} message(s) not shown; last: ${gist}`
      : `- ${stamp(first.at, tz, tzLabel)} call ${key}, ${ls.length} phrase(s) not shown; ${gist}`;
  });
}

export function render(lines: Line[], budgetTokens = DEFAULT_BUDGET_TOKENS, tz = 'UTC', tzLabel = 'UTC',
  title = 'THREAD', red: Redactor = redact): string {
  const head = `${title}: everything above this call, oldest first. Every line is [UTC time, ${tzLabel} time] then `
    + 'chat or call <id>, who, and the words. Newest lines are verbatim; older ones are summarized.';
  if (!lines.length) return `${head}\n(no earlier chat or calls in this thread)`;
  const sorted = [...lines].sort((a, b) => a.at - b.at);
  const summaryCap = Math.max(40, Math.trunc(budgetTokens * SUMMARY_SHARE));
  let room = budgetTokens - estimateTokens(head) - summaryCap;
  const kept: string[] = [];
  for (let i = sorted.length - 1; i >= 0; i--) {
    const s = formatLine(sorted[i], tz, tzLabel, red);
    const cost = estimateTokens(s) + 1;
    if (cost > room) break;
    kept.push(s);
    room -= cost;
  }
  kept.reverse();
  const dropped = sorted.slice(0, sorted.length - kept.length);
  const parts = [head];
  if (dropped.length) {
    const summ = summaries(dropped, tz, tzLabel, red);
    const block: string[] = [];
    let used = 0;
    for (let i = summ.length - 1; i >= 0; i--) {
      const c = estimateTokens(summ[i]) + 1;
      if (used + c > summaryCap) { block.push(`- (${summ.length - block.length} older item(s) omitted)`); break; }
      block.push(summ[i]);
      used += c;
    }
    block.reverse();
    parts.push('EARLIER (summarized):', ...block);
  }
  parts.push('RECENT (verbatim):', ...kept);
  return parts.join('\n');
}

/** Shaw's current zone from <data dir>/state/shaw-location.json, else UTC. */
export function shawTz(dataDir: string): [string, string] {
  try {
    const d = JSON.parse(fs.readFileSync(path.join(dataDir, 'state', 'shaw-location.json'), 'utf-8'));
    if (d?.tz) return [String(d.tz), String(d.label || d.tz)];
  } catch { /* absent */ }
  return ['UTC', 'UTC'];
}

/** The operator's own name (operator_store: <data dir>/state/arturo/operator.json), else "Operator". */
export function operatorName(dataDir: string): string {
  try {
    const d = JSON.parse(fs.readFileSync(path.join(dataDir, 'state', 'arturo', 'operator.json'), 'utf-8'));
    const v = d?.facts?.name?.value;
    if (typeof v === 'string' && strip(v)) return strip(v);
  } catch { /* absent */ }
  return 'Operator';
}

export interface BuildOptions {
  chatAgent: string; callOwner: string | null; items: any[] | null; journals: Journal[]; exclude?: string | null;
  budgetTokens?: number; tz: string; tzLabel: string; operatorLabel?: string; chatError?: string | null;
  redact?: Redactor;          // every piece, before it is clipped; default the twin's own shapes
}

/** The whole opening context (thread_context.load with its inputs already read). */
export function buildCallContext(o: BuildOptions): string {
  const op = o.operatorLabel ?? 'Operator';
  const notes: string[] = [];
  let lines: Line[] = [];
  if (o.items) lines = lines.concat(chatLines(o.items, o.chatAgent, op));
  else notes.push(`(${o.chatAgent} chat could not be read: ${o.chatError || 'Error'})`);
  lines = lines.concat(callLines(o.journals, o.callOwner, o.exclude ?? null, null, op));
  return [render(lines, o.budgetTokens ?? DEFAULT_BUDGET_TOKENS, o.tz, o.tzLabel, 'THREAD', o.redact ?? redact)]
    .concat(notes).join('\n');
}
