// Reply-to on the turn (docs/DESIGN_reply-threads-in-chat.md P1, the API half): <data dir>/state/reply-to.db, sqlite
// in WAL mode, Express-free so it is unit-tested with `tsx --test`.
//
// A person's send may name the message it answers: `reply_to: { uuid?, excerpt }` beside the words (the words ALREADY
// carry the one-line quote header the client built, `[Replying to <sender> <HH:MM> <zone>: "<excerpt>"] <words>`; the
// server never adds a second one). At a 2xx (delivered, steered, interrupted, held, stored) the route records
// {agent, sent_text, reply_to, message_id, ts} here. The transcript routes (the poll page, an older ?before= page, the
// SSE snapshot and its appends) then STAMP `reply_to` back onto the user-role text item whose words are that send: same
// agent, text equal to sent_text (or sent_text followed only by the server's own delivery tags, when a reader left
// them), written no earlier than STAMP_SKEW_MS before the send and no later than STAMP_WINDOW_MS after it. More words
// after the send ("ok" vs "okay then") are a different message and never match; a line from before the send is an
// earlier message that happens to read the same. The client jumps by `item.reply_to.uuid`; without the stamp it falls
// back to matching the quoted words.
//
// sent_replies(agent, sent_text, reply_uuid, excerpt, message_id, ts): one row per recorded send. Rows older than
// KEEP_DAYS are pruned on write. Everything here is fail-open for readers: a store that cannot be opened or read
// leaves the items as they were (the stamp is a convenience, never the chat).
import Database, { type Database as DB } from 'better-sqlite3';
import { mkdirSync } from 'fs';
import { dirname, join } from 'path';
import { dataDir } from './config.js';

/** What the client sends beside the words, after validation. */
export interface ReplyTo { uuid?: string; excerpt: string }

/** A recorded send. */
export interface SentReply { agent: string; sent_text: string; reply_to: ReplyTo; message_id?: string; ts: string }

/** A transcript line uuid as the server names one (Claude's uuid, Codex's ids, the stitched `<session>~<uuid>` refs). */
export const REPLY_UUID_RE = /^[A-Za-z0-9_.:~#-]{1,200}$/;
export const EXCERPT_MAX = 200;
/** A stamp matches a send this long after it: the transcript line's `ts` is the CLI's, the row's is the API's. */
export const STAMP_WINDOW_MS = 24 * 3600 * 1000;
/** ...and at most this long BEFORE it: the line is written when the words reach the agent, never before the send;
 *  the allowance covers the two clocks (the API's at record time, the CLI's at the line) disagreeing a little. */
export const STAMP_SKEW_MS = 60 * 1000;
/** What may follow the words on a transcript line and still be this send: the server's own delivery tags
 *  (routes/agent-send.ts pageContextLine + VOICE_CALL_LIVE_TAG), whitespace-separated, nothing else. The readers strip
 *  them (routes/chat-transcript.ts PAGE_CONTEXT_TAG, VOICE_CALL_LIVE_TAG_RE); this is for a line a reader left whole. */
const SERVER_TAGS_ONLY_RE = /^(?:\s+<(page-context|voice-call-live)>[\s\S]*?<\/\1>)+\s*$/;
const KEEP_DAYS = 7;
/** How many recent sends per agent a stamp pass reads (a window of a few hundred items spans far fewer sends). */
const RECENT_PER_AGENT = 400;

/** The body's `reply_to`, validated: a string uuid of the allowed shape (else dropped, the excerpt kept), an excerpt
 *  string trimmed to EXCERPT_MAX (else the whole field is dropped). Anything that is not an object: undefined. */
export function validateReplyTo(raw: unknown): ReplyTo | undefined {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return undefined;
  const r = raw as Record<string, unknown>;
  if (typeof r.excerpt !== 'string') return undefined;
  const excerpt = r.excerpt.replace(/[\r\n]+/g, ' ').trim().slice(0, EXCERPT_MAX);
  if (!excerpt) return undefined;
  const uuid = typeof r.uuid === 'string' && REPLY_UUID_RE.test(r.uuid) ? r.uuid : undefined;
  return uuid ? { uuid, excerpt } : { excerpt };
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS sent_replies (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  agent      TEXT NOT NULL,
  sent_text  TEXT NOT NULL,
  reply_uuid TEXT,
  excerpt    TEXT NOT NULL,
  message_id TEXT,
  ts         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sent_replies_agent_ts ON sent_replies (agent, ts);`;

interface Row { sent_text: string; reply_uuid: string | null; excerpt: string; message_id: string | null; ts: string }

export function replyToDbPath(root: string = dataDir()): string {
  return join(root, 'state', 'reply-to.db');
}

export class ReplyToStore {
  private readonly db: DB;

  constructor(readonly path: string) {
    mkdirSync(dirname(path), { recursive: true });
    this.db = new Database(path);
    this.db.pragma('journal_mode = WAL');
    this.db.pragma('busy_timeout = 3000');
    this.db.exec(SCHEMA);
  }

  /** Keep a send's reply_to on its turn. Prunes rows older than KEEP_DAYS. */
  record(r: SentReply): void {
    this.db.prepare(
      `INSERT INTO sent_replies (agent, sent_text, reply_uuid, excerpt, message_id, ts) VALUES (?, ?, ?, ?, ?, ?)`,
    ).run(r.agent, r.sent_text, r.reply_to.uuid ?? null, r.reply_to.excerpt, r.message_id ?? null, r.ts);
    const cutoff = new Date(Date.parse(r.ts) - KEEP_DAYS * 86400 * 1000);
    if (!Number.isNaN(cutoff.getTime())) this.db.prepare(`DELETE FROM sent_replies WHERE ts < ?`).run(cutoff.toISOString());
  }

  /** The newest recorded sends for an agent, newest first. */
  recent(agent: string, limit: number = RECENT_PER_AGENT): SentReply[] {
    const rows = this.db.prepare(
      `SELECT sent_text, reply_uuid, excerpt, message_id, ts FROM sent_replies WHERE agent = ? ORDER BY ts DESC, id DESC LIMIT ?`,
    ).all(agent, limit) as Row[];
    return rows.map((r) => ({
      agent, sent_text: r.sent_text, ts: r.ts,
      reply_to: r.reply_uuid ? { uuid: r.reply_uuid, excerpt: r.excerpt } : { excerpt: r.excerpt },
      ...(r.message_id ? { message_id: r.message_id } : {}),
    }));
  }

  close(): void {
    this.db.close();
  }
}

const stores = new Map<string, ReplyToStore>();

/** The process-wide store for a path (default: this install's reply-to.db). Opened on first use. */
export function replyToStore(path: string = replyToDbPath()): ReplyToStore {
  let s = stores.get(path);
  if (!s) { s = new ReplyToStore(path); stores.set(path, s); }
  return s;
}

/** The items a stamp may land on: the operator's own words (a user-role text item no machine wrote), with text. */
function stampable(it: any): boolean {
  return !!it && it.kind === 'text' && it.role === 'user' && typeof it.text === 'string' && !it.machine && !it.reply_to;
}

/** The item's text is this send's words: equal, or the words followed only by the server's delivery tags. */
function sameWords(text: string, sent: string): boolean {
  if (text === sent) return true;
  return text.length > sent.length && text.startsWith(sent) && SERVER_TAGS_ONLY_RE.test(text.slice(sent.length));
}

/** The item was written when this send landed: no earlier than STAMP_SKEW_MS before it, no later than
 *  STAMP_WINDOW_MS after. An item or a send with no parsable time matches on the words alone. */
function inWindow(itemMs: number, s: SentReply): boolean {
  if (Number.isNaN(itemMs)) return true;
  const sentMs = Date.parse(s.ts);
  if (Number.isNaN(sentMs)) return true;
  const delta = itemMs - sentMs;
  return delta >= -STAMP_SKEW_MS && delta <= STAMP_WINDOW_MS;
}

/** `reply_to` onto the items whose words are a recorded send of this agent (same agent; the item's text equal to the
 *  recorded sent_text, or to it plus the server's own tags; the item's `ts`, when it has one, no earlier than
 *  STAMP_SKEW_MS before the send and no later than STAMP_WINDOW_MS after it). An exact match beats a tagged one; among
 *  equals the newest send wins. Items are returned as new objects where stamped, untouched otherwise. Fail-open: any
 *  trouble (no store, an unreadable file) returns the items as they were. */
export function stampReplyTo(items: any[], agentId: string, sends?: SentReply[] | null): any[] {
  let recent: SentReply[];
  try {
    recent = sends ?? replyToStore().recent(agentId);
  } catch {
    return items;
  }
  if (!recent.length || !items.some(stampable)) return items;
  const mine = recent.filter((s) => s.agent === agentId && !!s.sent_text);
  if (!mine.length) return items;
  return items.map((it) => {
    if (!stampable(it)) return it;
    const itemMs = typeof it.ts === 'string' ? Date.parse(it.ts) : NaN;
    const s = mine.find((c) => it.text === c.sent_text && inWindow(itemMs, c))
      ?? mine.find((c) => sameWords(it.text, c.sent_text) && inWindow(itemMs, c));
    if (!s) return it;
    return { ...it, reply_to: { ...(s.reply_to.uuid ? { uuid: s.reply_to.uuid } : {}), excerpt: s.reply_to.excerpt } };
  });
}
