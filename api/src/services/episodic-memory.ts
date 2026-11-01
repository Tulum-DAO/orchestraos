/**
 * episodic-memory.ts — read side of the conversation index (scripts/episodic_memory.py owns the store and writes it):
 * the `episodic` source of GET /api/memory/search.
 *
 * Read only (the database is opened readonly and never created here). What it may return is never more than the
 * transcript routes would show the same caller: only agents the caller can see (lib/agent-scope.ts, the rule every
 * /:id transcript route applies), and per row only a snippet around the match with its tags and ids, never the
 * whole stored text.
 */
import Database from 'better-sqlite3';
import { existsSync } from 'fs';
import { join } from 'path';
import { loadConfig } from '../lib/config.js';

export interface EpisodicHit {
  source: 'episodic';
  score: number;
  agent: string;
  session_id: string;
  generation: number | null;
  uuid: string;
  part: number;
  tool_id: string | null;
  role: string | null;
  kind: string;
  ts: string | null;
  snippet: string;
}

export function episodicDbPath(): string {
  const data = process.env.ORCHESTRA_DIR || loadConfig().dataDir;
  return join(data, 'state', 'episodic-memory.db');
}

/** Each word quoted (the Python side's rule), so FTS syntax in a question is read as words. */
export function ftsQuery(q: string): string {
  return q.trim().split(/\s+/).filter(Boolean).map((w) => `"${w.replace(/"/g, '""')}"`).join(' ');
}

/** BM25 is lower-is-better and unbounded; the search route sorts sources together on 0–100, so a best match maps
 *  near the top of that scale and weaker ones below it, keeping their order. */
const scoreOf = (rank: number) => Math.round(Math.max(1, Math.min(95, 50 - rank * 5)));

export function searchEpisodic(
  q: string,
  opts: { agent?: string; session?: string; generation?: number; limit?: number },
  canSee: (agent: string) => boolean,
  dbPath: string = episodicDbPath(),
): EpisodicHit[] {
  const fts = ftsQuery(q);
  if (!fts || !existsSync(dbPath)) return [];
  const db = new Database(dbPath, { readonly: true, fileMustExist: true });
  try {
    const visible = (db.prepare('SELECT DISTINCT agent FROM entries').all() as { agent: string }[]).map((r) => r.agent).filter(canSee);
    const agents = opts.agent ? visible.filter((a) => a === opts.agent) : visible;
    if (!agents.length) return [];
    const where = ['entries_fts MATCH ?', `e.agent IN (${agents.map(() => '?').join(',')})`];
    const args: unknown[] = [fts, ...agents];
    if (opts.session) { where.push('e.session_id = ?'); args.push(opts.session); }
    if (opts.generation !== undefined) { where.push('e.generation = ?'); args.push(opts.generation); }
    const limit = Math.max(1, Math.min(opts.limit ?? 20, 200));
    const rows = db.prepare(`SELECT e.agent, e.session_id, e.generation, e.uuid, e.part, e.tool_id, e.role, e.kind, e.ts,
        snippet(entries_fts, 0, '[', ']', ' … ', 24) AS snippet, bm25(entries_fts) AS rank
      FROM entries_fts JOIN entries e ON e.id = entries_fts.rowid WHERE ${where.join(' AND ')} ORDER BY rank LIMIT ?`)
      .all(...args, limit) as (Omit<EpisodicHit, 'source' | 'score'> & { rank: number })[];
    return rows.map(({ rank, ...r }) => ({ source: 'episodic' as const, score: scoreOf(rank), ...r }));
  } finally {
    db.close();
  }
}
