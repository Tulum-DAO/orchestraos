/**
 * conversation-store.ts — what the API reads from the conversation store (scripts/episodic_memory.py owns it and
 * writes it): the rules that keep what a provider session only SAW out of everything we show, and the compaction
 * notices.
 *
 *   seen_only      (session, uuid): that entry carried the earlier-conversation block a new session is given;
 *                  `stripBlock` removes it from THAT entry only (the same words typed by a person elsewhere stay).
 *   seed_boundary  (session, through_uuid): rows at or before it are seed, never shown.
 *   compaction_notices: the chat's "Chat compacting…" / "Chat compacted" notice, per seat.
 *
 * "Never saved" is enforced by OUR readers only (the chat route, the conversation index, library attribution and
 * summaries skip seen-only blocks and seed rows); the CLI writes the block into its own session file and nothing in
 * that file is altered. Read only, cached until the database changes, fail-open: no store, nothing to apply.
 */
import Database from 'better-sqlite3';
import { existsSync, statSync } from 'fs';
import { join } from 'path';
import { loadConfig } from '../lib/config.js';

export const BLOCK_OPEN = '<earlier-conversation>';
export const BLOCK_CLOSE = '</earlier-conversation>';
/** The blocks a session is given that are not the conversation: the earlier conversation a new session starts from
 *  (the fallback path), and the short note after a person's /compact did not go through. Same rule for both. */
const MARKERS: [string, RegExp][] = [
  ['<earlier-conversation>', /<earlier-conversation>[\s\S]*?<\/earlier-conversation>[ \t]*\n?/g],
  ['<compact-note>', /<compact-note>[\s\S]*?<\/compact-note>[ \t]*\n?/g],
];
export const hasBlock = (text: string): boolean => !!text && MARKERS.some(([o]) => text.includes(o));

/** The text without its earlier-conversation and compact-note blocks (an unclosed one runs to the end), trimmed of the
 *  blank lines they leave. The TS port of episodic_memory.strip_block; strip_seen's choice of entry is
 *  `applyStoreRules`'s. */
export function stripBlock(text: string): string {
  if (!hasBlock(text)) return text;
  let out = text;
  for (const [open, rx] of MARKERS) {
    out = out.replace(rx, '');
    const k = out.indexOf(open);
    if (k >= 0) out = out.slice(0, k);
  }
  return out.replace(/^\n+|\n+$/g, '');
}

export interface StoreRules { seen: ReadonlySet<string>; through: string | null; sig: string }
export interface CompactionNotice {
  id: number; agent: string; state: 'compacting' | 'compacted' | 'failed'; started_at: string; finished_at: string | null;
  before_tokens: number | null; after_tokens: number | null; from_session: string | null; to_session: string | null;
  trigger?: 'auto' | 'manual' | null;
}

/** Whether a notice is shown at all: a failed one only when a person's /compact started it ("Couldn't compact the chat
 *  right now."). An automatic notice is only ever made once the switch is committed, so it never fails; one that did
 *  anyway is not shown. */
export const noticeShown = (n: CompactionNotice): boolean => n.state !== 'failed' || n.trigger === 'manual';

const NO_RULES: StoreRules = { seen: new Set(), through: null, sig: '' };

export function storePath(dataDir?: string): string {
  return join(dataDir ?? (process.env.ORCHESTRA_DIR || loadConfig().dataDir), 'state', 'episodic-memory.db');
}

/** A signature that changes whenever the store is written (the WAL takes writes before a checkpoint). */
function sigOf(p: string): string {
  try {
    const a = statSync(p);
    let w = '';
    try { const b = statSync(`${p}-wal`); w = `:${b.size}:${b.mtimeMs}`; } catch { /* no WAL */ }
    return `${a.size}:${a.mtimeMs}${w}`;
  } catch { return ''; }
}

const cache = new Map<string, { sig: string; value: unknown }>();

function cached<T>(p: string, key: string, read: (db: Database.Database) => T, empty: T): T {
  if (!existsSync(p)) return empty;
  const sig = sigOf(p);
  const k = `${p}|${key}`;
  const hit = cache.get(k);
  if (hit && hit.sig === sig) return hit.value as T;
  let value = empty;
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    value = read(db);
  } catch { value = empty; } finally { db?.close(); }
  cache.set(k, { sig, value });
  return value;
}

/** Tests: forget what was read. */
export function forgetStoreCache(): void { cache.clear(); }

const hasTable = (db: Database.Database, t: string) => !!db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(t);

/** The seen-only entries and seed boundary of one provider session. */
export function storeRules(session: string | null | undefined, p: string = storePath()): StoreRules {
  if (!session) return NO_RULES;
  return cached(p, `rules:${session}`, (db) => {
    const seen = hasTable(db, 'seen_only')
      ? new Set((db.prepare('SELECT uuid FROM seen_only WHERE provider_session_id = ?').all(session) as { uuid: string }[]).map((r) => r.uuid))
      : new Set<string>();
    const b = hasTable(db, 'seed_boundary')
      ? db.prepare('SELECT through_uuid FROM seed_boundary WHERE provider_session_id = ?').get(session) as { through_uuid: string } | undefined
      : undefined;
    if (!seen.size && !b) return NO_RULES;
    return { seen, through: b?.through_uuid ?? null, sig: `${seen.size}:${[...seen].sort().join(',').length}:${b?.through_uuid ?? ''}:${sigOf(p)}` };
  }, NO_RULES);
}

/** A session's chat items with the store's rules applied: seed items (at or before the boundary's last item; all of
 *  them while the boundary has not been written yet) left out, and the block removed from each seen-only entry (an
 *  entry that was only the block is left out). Items are copied when changed, never edited in place. */
export function applyStoreRules<T extends Record<string, any>>(items: readonly T[], rules: StoreRules): T[] {
  if (!rules.seen.size && !rules.through) return items as T[];
  let start = 0;
  if (rules.through) {
    start = items.length;
    for (let i = items.length - 1; i >= 0; i--) if (items[i]?.uuid === rules.through) { start = i + 1; break; }
  }
  const out: T[] = [];
  for (let i = start; i < items.length; i++) {
    const it = items[i];
    if (it?.uuid && rules.seen.has(String(it.uuid)) && typeof it.text === 'string' && hasBlock(it.text)) {
      const text = stripBlock(it.text);
      if (!text.trim()) continue;
      out.push({ ...it, text });
    } else {
      out.push(it);
    }
  }
  return out;
}

/** The same rules on raw Claude Code JSONL lines (library attribution reads lines, not chat items): seed lines left
 *  out, and the block removed from the text of each seen-only line. `seedDone`: the boundary line was already read in
 *  an earlier chunk of this file. Returns the lines and whether the boundary has now been passed. */
export function applyRulesToLines(lines: string[], rules: StoreRules, seedDone = false): { lines: string[]; seedDone: boolean } {
  if (!rules.seen.size && !rules.through) return { lines, seedDone: true };
  let done = seedDone || !rules.through;
  const out: string[] = [];
  for (const raw of lines) {
    let o: any = null;
    try { o = JSON.parse(raw); } catch { if (done) out.push(raw); continue; }
    if (!done) {
      if (o?.uuid === rules.through) done = true;
      continue;
    }
    if (o?.uuid && rules.seen.has(String(o.uuid)) && hasBlock(raw)) {
      const c = o.message?.content;
      if (typeof c === 'string') o.message.content = stripBlock(c);
      else if (Array.isArray(c)) for (const b of c) if (b && typeof b.text === 'string') b.text = stripBlock(b.text);
      out.push(JSON.stringify(o));
      continue;
    }
    out.push(raw);
  }
  return { lines: out, seedDone: done };
}

/** The seat's compaction notices, oldest first. */
export function compactionNotices(agent: string, p: string = storePath()): CompactionNotice[] {
  return cached(p, `notices:${agent}`, (db) => hasTable(db, 'compaction_notices')
    ? db.prepare('SELECT * FROM compaction_notices WHERE agent = ? ORDER BY started_at, id').all(agent) as CompactionNotice[]
    : [], [] as CompactionNotice[]);
}

/** A notice as the chat's item (and as the data of an SSE `event: compaction`): one per notice, the same id as it
 *  updates. The text a client draws is its own ("Chat compacting…" / "Chat compacted"); token counts stay data. */
export function compactionItem(n: CompactionNotice): Record<string, unknown> {
  return {
    kind: 'compaction', id: `cmp-${n.id}`, uuid: `cmp-${n.id}`, state: n.state, trigger: n.trigger === 'manual' ? 'manual' : 'auto', ts: n.started_at,
    finished_at: n.finished_at ?? null, before_tokens: n.before_tokens ?? null, after_tokens: n.after_tokens ?? null, audience: 'user',
  };
}

/** Notices placed into a window of chat items by time: each goes before the first item that is newer than it.
 *  `range`: only notices at or after `from` and before `to` (either may be null: open on that side). */
export function mergeNotices(items: any[], notices: readonly CompactionNotice[], range: { from: string | null; to: string | null }): any[] {
  const ms = (v: string | null) => (v ? Date.parse(v) : NaN);
  const from = ms(range.from), to = ms(range.to);
  const pick = notices.filter(noticeShown).filter((n) => {
    const t = ms(n.started_at);
    return (Number.isNaN(from) || t >= from) && (Number.isNaN(to) || t < to);
  });
  if (!pick.length) return items;
  const out = items.slice();
  for (const n of pick) {
    const t = Date.parse(n.started_at);
    let at = out.length;
    for (let i = 0; i < out.length; i++) {
      const it = Date.parse(String(out[i]?.ts ?? ''));
      if (!Number.isNaN(it) && it > t) { at = i; break; }
    }
    out.splice(at, 0, compactionItem(n));
  }
  return out;
}
