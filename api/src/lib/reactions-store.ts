// The reactions store (spec "Data"): <data dir>/state/reactions.db, sqlite in WAL mode. Express-free so it can be
// unit-tested with `tsx --test`.
//
// reactions(surface, conv, ref, reactor_kind, reactor_id, emoji, at), PRIMARY KEY(surface, conv, ref, reactor_kind,
// reactor_id): a reactor has at most ONE emoji per message, so a new pick is an upsert and the same pick again is a
// delete (toggle). `at` is UTC ISO. Refs per surface: agent-chat `<session>~<uuid>#<part>` (lib/messageRef.ts),
// arturo the turn `seq`, call the journal turn index (build step 4).
import Database, { type Database as DB } from 'better-sqlite3';
import { mkdirSync } from 'fs';
import { dirname, join } from 'path';
import { dataDir } from './config.js';

export const SURFACES = ['agent-chat', 'arturo', 'call'] as const;
export type Surface = (typeof SURFACES)[number];

/** Who reacted: a user (the principal) or an agent that proved itself with the react token (lib/reactor.ts). */
export interface Reactor { kind: 'user' | 'agent'; id: string }

export interface ReactionKey { surface: Surface; conv: string; ref: string; reactor: Reactor }

/** What changed, as the PUT/DELETE answer and the SSE `reaction` event carry it. emoji null = removed. */
export interface ReactionChange { surface: Surface; conv: string; ref: string; reactor: Reactor; emoji: string | null; at: string }

export interface StoredReaction { emoji: string; reactor: Reactor; at: string }

const SCHEMA = `
CREATE TABLE IF NOT EXISTS reactions (
  surface      TEXT NOT NULL,
  conv         TEXT NOT NULL,
  ref          TEXT NOT NULL,
  reactor_kind TEXT NOT NULL CHECK (reactor_kind IN ('user', 'agent')),
  reactor_id   TEXT NOT NULL,
  emoji        TEXT NOT NULL,
  at           TEXT NOT NULL,
  PRIMARY KEY (surface, conv, ref, reactor_kind, reactor_id)
);`;

const WHERE_KEY = 'surface = ? AND conv = ? AND ref = ? AND reactor_kind = ? AND reactor_id = ?';

interface Row { ref: string; reactor_kind: 'user' | 'agent'; reactor_id: string; emoji: string; at: string }

function keyArgs(k: ReactionKey): string[] {
  return [k.surface, k.conv, k.ref, k.reactor.kind, k.reactor.id];
}

function change(k: ReactionKey, emoji: string | null, at: string): ReactionChange {
  return { surface: k.surface, conv: k.conv, ref: k.ref, reactor: { kind: k.reactor.kind, id: k.reactor.id }, emoji, at };
}

export function reactionsDbPath(root: string = dataDir()): string {
  return join(root, 'state', 'reactions.db');
}

export class ReactionStore {
  private readonly db: DB;

  constructor(readonly path: string) {
    mkdirSync(dirname(path), { recursive: true });
    this.db = new Database(path);
    this.db.pragma('journal_mode = WAL');
    this.db.pragma('busy_timeout = 3000');
    this.db.exec(SCHEMA);
  }

  /** Every reaction in one conversation, grouped by message ref, oldest first. */
  list(surface: Surface, conv: string): Record<string, StoredReaction[]> {
    const rows = this.db.prepare(
      `SELECT ref, reactor_kind, reactor_id, emoji, at FROM reactions
       WHERE surface = ? AND conv = ? ORDER BY at, reactor_kind, reactor_id`,
    ).all(surface, conv) as Row[];
    const out: Record<string, StoredReaction[]> = {};
    for (const r of rows) {
      (out[r.ref] ??= []).push({ emoji: r.emoji, reactor: { kind: r.reactor_kind, id: r.reactor_id }, at: r.at });
    }
    return out;
  }

  /** Set this reactor's emoji on the message; the same emoji it already has removes it (emoji null in the result). */
  toggle(key: ReactionKey, emoji: string, at: string = new Date().toISOString()): ReactionChange {
    const args = keyArgs(key);
    const tx = this.db.transaction((): ReactionChange => {
      const cur = this.db.prepare(`SELECT emoji FROM reactions WHERE ${WHERE_KEY}`).get(...args) as { emoji: string } | undefined;
      if (cur && cur.emoji === emoji) {
        this.db.prepare(`DELETE FROM reactions WHERE ${WHERE_KEY}`).run(...args);
        return change(key, null, at);
      }
      this.db.prepare(
        `INSERT INTO reactions (surface, conv, ref, reactor_kind, reactor_id, emoji, at) VALUES (?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT (surface, conv, ref, reactor_kind, reactor_id) DO UPDATE SET emoji = excluded.emoji, at = excluded.at`,
      ).run(...args, emoji, at);
      return change(key, emoji, at);
    });
    return tx.immediate();
  }

  /** Remove this reactor's reaction; null when it had none. */
  remove(key: ReactionKey, at: string = new Date().toISOString()): ReactionChange | null {
    const r = this.db.prepare(`DELETE FROM reactions WHERE ${WHERE_KEY}`).run(...keyArgs(key));
    return r.changes > 0 ? change(key, null, at) : null;
  }

  close(): void {
    this.db.close();
  }
}

const stores = new Map<string, ReactionStore>();

/** The process-wide store for a path (default: this install's reactions.db). Opened on first use. */
export function reactionStore(path: string = reactionsDbPath()): ReactionStore {
  let s = stores.get(path);
  if (!s) { s = new ReactionStore(path); stores.set(path, s); }
  return s;
}
