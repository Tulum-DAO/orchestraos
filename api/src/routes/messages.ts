import { Router, type Request, type Response } from 'express';
import { execFileSync } from 'child_process';
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import Database from 'better-sqlite3';
import { loadConfig } from '../lib/config.js';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR || loadConfig().dataDir;   // DATA dir

/** msg_store.py is CODE: ORCHESTRA_ROOT > the checkout this module lives in
 *  (<root>/api/dist/routes/messages.js). Never the data dir (a fresh install has no code there). */
export function resolveMsgStorePath(env: Record<string, string | undefined>, moduleUrl: string): string {
  if (env.ORCHESTRA_ROOT) return join(env.ORCHESTRA_ROOT, 'msg_store.py');
  const here = dirname(fileURLToPath(moduleUrl));
  return join(here, '..', '..', '..', 'msg_store.py');
}
const BUS = resolveMsgStorePath(process.env, import.meta.url);

export interface RecentMessage {
  id: string; conversation_id: string | null; from_agent: string; to_agent: string; type: string | null;
  subject: string | null; priority: string | null; status: string | null; created_at: string | null;
  delivered_at: string | null; acknowledged_at: string | null;
}

/** Latest seat-to-seat rows across every seat, newest first, straight from <data>/state/tasks.db.
 *  Both directions of an exchange show up (A->B and B->A). Missing db/table => []. */
export function recentMessages(dataDir: string, limit = 50): RecentMessage[] {
  const dbPath = join(dataDir, 'state', 'tasks.db');
  if (!existsSync(dbPath)) return [];
  try {
    const db = new Database(dbPath, { readonly: true, fileMustExist: true });
    try {
      const rows = db.prepare(
        `select id, conversation_id, from_agent, to_agent, type, subject, priority, status, created_at,
                delivered_at, acknowledged_at
           from messages where archived_at is null order by created_at desc limit ?`).all(limit) as RecentMessage[];
      return rows;
    } finally { db.close(); }
  } catch { return []; }
}

/* ── Canonical per-pair message counts and history ───────────────────────────
 * 2D Agents View spec §16 (architecture lock) pins BOTH the connection-line
 * count and the conversation panel to ONE backend: the SQLite `messages` table
 * in <data>/state/tasks.db — the same table recentMessages() above reads. The
 * other three stores in this repo already disagree with each other (tasks.db's
 * iteration_count is only bumped in reply() not send(); the per-agent JSONL
 * logs drift from the per-conversation JSONL; the queue/inbox JSON store
 * truncates to 20 while reporting an untruncated total). The spec's "49 vs 40"
 * bug is what picking two of them looks like, so: one table, both numbers.
 *
 * Time comparisons all go through julianday(). Every row today is uniform
 * ISO-8601 with a +00:00 offset (verified: 871/871, and julianday() parses all
 * of them), but the column DEFAULT is datetime('now') — which writes
 * 'YYYY-MM-DD HH:MM:SS', no 'T' and no offset. A lexicographic compare would
 * sort any such row before every ISO row (space < 'T') and silently drop it
 * from the window. julianday() is format-agnostic, and at this table's size the
 * lost index is not worth a correctness hole.
 *
 * Known ceiling: pairCounts' `last_at` is a plain max(created_at), so it IS a
 * lexicographic pick. If a DEFAULT-format row is ever the newest on a line, the
 * "last message" in the line tooltip reads slightly stale — counts and ordering
 * stay correct, only that one label. Upgrade when it matters: select the
 * created_at of the row with max(julianday(created_at)) instead of the string max.
 */

const PAIR_COLUMNS = `id, conversation_id, from_agent, to_agent, type, subject, body, priority,
                      status, created_at, delivered_at, acknowledged_at`;

export interface PairCount { a: string; b: string; count: number; last_at: string | null; }

function openMessagesDb(dataDir: string): Database.Database | null {
  const dbPath = join(dataDir, 'state', 'tasks.db');
  if (!existsSync(dbPath)) return null;
  try { return new Database(dbPath, { readonly: true, fileMustExist: true }); } catch { return null; }
}

function cutoffIso(windowHours: number): string {
  return new Date(Date.now() - windowHours * 3600_000).toISOString();
}

/** Message count per UNORDERED agent pair inside the window, newest activity first.
 *  Both directions of an exchange collapse into one row, because a connection line is
 *  one line. Self-sends (from === to) are excluded: they are real rows, but a line needs
 *  two ends. Missing db/table => []. */
export function pairCounts(dataDir: string, windowHours = 24): PairCount[] {
  const db = openMessagesDb(dataDir);
  if (!db) return [];
  try {
    return db.prepare(
      `select min(from_agent, to_agent) as a, max(from_agent, to_agent) as b,
              count(*) as count, max(created_at) as last_at
         from messages
        where archived_at is null
          and from_agent <> to_agent
          and julianday(created_at) >= julianday(?)
        group by a, b
        order by count desc`).all(cutoffIso(windowHours)) as PairCount[];
  } catch { return []; } finally { db.close(); }
}

export interface PairPage {
  a: string; b: string;
  /** Matches the connection-line label exactly — same table, same window. */
  total_in_window: number;
  /** What "load older" can eventually reach. Pagination is NOT capped by the window. */
  total_all_time: number;
  window_hours: number;
  messages: Record<string, unknown>[];
  next_before: string | null;
  has_more: boolean;
}

/** One pair's history, newest first, paged by a created_at cursor.
 *  `before` returns rows strictly older than that instant, so passing back the previous
 *  response's next_before walks history without re-sending the boundary row. */
export function pairMessages(
  dataDir: string, a: string, b: string,
  limit = 40, before?: string | null, windowHours = 24,
): PairPage {
  const empty: PairPage = {
    a, b, total_in_window: 0, total_all_time: 0, window_hours: windowHours,
    messages: [], next_before: null, has_more: false,
  };
  const db = openMessagesDb(dataDir);
  if (!db) return empty;
  try {
    const pair = `archived_at is null
                    and ((from_agent = ? and to_agent = ?) or (from_agent = ? and to_agent = ?))`;
    const ends = [a, b, b, a];
    const totalAllTime = (db.prepare(`select count(*) as n from messages where ${pair}`)
      .get(...ends) as { n: number }).n;
    const totalInWindow = (db.prepare(
      `select count(*) as n from messages where ${pair} and julianday(created_at) >= julianday(?)`)
      .get(...ends, cutoffIso(windowHours)) as { n: number }).n;

    // limit + 1 so has_more is observed, not inferred from a full page.
    const rows = before
      ? db.prepare(`select ${PAIR_COLUMNS} from messages
                     where ${pair} and julianday(created_at) < julianday(?)
                     order by julianday(created_at) desc limit ?`).all(...ends, before, limit + 1)
      : db.prepare(`select ${PAIR_COLUMNS} from messages
                     where ${pair}
                     order by julianday(created_at) desc limit ?`).all(...ends, limit + 1);
    const page = (rows as Record<string, unknown>[]).slice(0, limit);
    const hasMore = (rows as unknown[]).length > limit;
    return {
      a, b,
      total_in_window: totalInWindow,
      total_all_time: totalAllTime,
      window_hours: windowHours,
      messages: page,
      next_before: hasMore && page.length ? String(page[page.length - 1].created_at) : null,
      has_more: hasMore,
    };
  } catch { return empty; } finally { db.close(); }
}

function runBus(args: (string | string[])[]): any {
  const flatArgs = args.flat() as string[];
  try {
    const out = execFileSync('python3', [BUS, ...flatArgs], {
      encoding: 'utf-8',
      timeout: 10000,
      cwd: ORCHESTRA,
    });
    try { return JSON.parse(out); } catch { return { raw: out.trim() }; }
  } catch (err: any) {
    return { error: err.message };
  }
}

// POST /api/messages/send — send a message between agents
router.post('/send', (req: Request, res: Response) => {
  const { from, to, subject, body, priority, type, conversation_id } = req.body;
  if (!from || !to || !subject) {
    res.status(400).json({ error: 'from, to, and subject required' });
    return;
  }
  const args = ['send', '--from', from, '--to', to, '--subject', subject];
  if (body) args.push('--body', body);
  if (priority) args.push('--priority', priority);
  if (type) args.push('--type', type);
  if (conversation_id) args.push('--conversation-id', conversation_id);
  res.json(runBus(args));
});

// POST /api/messages/reply — reply to a message (auto-routes back to sender)
router.post('/reply', (req: Request, res: Response) => {
  const { message_id, body, from } = req.body;
  if (!message_id || !body) {
    res.status(400).json({ error: 'message_id and body required' });
    return;
  }
  const args = ['reply', '--message-id', message_id, '--body', body];
  if (from) args.push('--from', from);
  res.json(runBus(args));
});

// GET /api/messages/inbox/:agentId — get unread messages for an agent
router.get('/inbox/:agentId', (req: Request, res: Response) => {
  const { agentId } = req.params;
  const all = req.query.all === 'true';
  const args = ['inbox', '--agent', agentId];
  if (all) args.push('--all');
  res.json(runBus(args));
});

// GET /api/messages/recent?limit=50 — latest seat-to-seat mail across all seats (web Inbox "Mail")
router.get('/recent', (req: Request, res: Response) => {
  const limit = Math.min(200, Math.max(1, parseInt(String(req.query.limit || '50'), 10) || 50));
  res.json({ messages: recentMessages(ORCHESTRA, limit) });
});

function windowHoursFrom(q: unknown): number {
  const h = parseInt(String(q ?? '24'), 10);
  return Number.isFinite(h) && h > 0 ? Math.min(24 * 90, h) : 24;
}

// GET /api/messages/pair-counts?hours=24 — message count per connection line.
// The ONLY source for any count rendered on a line (spec §16).
router.get('/pair-counts', (req: Request, res: Response) => {
  const hours = windowHoursFrom(req.query.hours);
  res.json({ window_hours: hours, pairs: pairCounts(ORCHESTRA, hours) });
});

// GET /api/messages/pair/:a/:b?limit=40&before=<iso>&hours=24 — one pair's history for the
// conversation panel. Same table as /pair-counts, so the header count and the line agree
// by construction; `before` walks past the window, total_in_window does not.
router.get('/pair/:a/:b', (req: Request, res: Response) => {
  const limit = Math.min(200, Math.max(1, parseInt(String(req.query.limit || '40'), 10) || 40));
  const before = req.query.before ? String(req.query.before) : null;
  res.json(pairMessages(ORCHESTRA, String(req.params.a), String(req.params.b),
    limit, before, windowHoursFrom(req.query.hours)));
});

// GET /api/messages/thread/:conversationId — get full conversation thread
router.get('/thread/:conversationId', (req: Request, res: Response) => {
  const { conversationId } = req.params;

  // Read conversation JSONL directly for structured data
  const convFile = join(ORCHESTRA, 'state', 'conversations', `${conversationId}.jsonl`);
  try {
    if (!existsSync(convFile)) { res.json({ conversation_id: conversationId, messages: [], count: 0 }); return; }
    const lines = readFileSync(convFile, 'utf-8').trim().split('\n');
    const messages = lines
      .filter((l: string) => l.trim())
      .map((l: string) => { try { return JSON.parse(l); } catch { return null; } })
      .filter(Boolean)
      .sort((a: any, b: any) => (a.created || '').localeCompare(b.created || ''));
    res.json({ conversation_id: conversationId, messages, count: messages.length });
  } catch {
    res.json({ conversation_id: conversationId, messages: [], count: 0 });
  }
});

// GET /api/messages/conversations/:agentId — list all conversations for an agent
router.get('/conversations/:agentId', (req: Request, res: Response) => {
  const { agentId } = req.params;

  // Read agent's message log and aggregate by conversation
  const logFile = join(ORCHESTRA, 'state', 'messages', `${agentId}.jsonl`);
  try {
    if (!existsSync(logFile)) { res.json({ agent_id: agentId, conversations: [], total: 0 }); return; }
    const lines = readFileSync(logFile, 'utf-8').trim().split('\n');
    const convs: Record<string, any> = {};

    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const msg = JSON.parse(line);
        const cid = msg.conversation_id || 'unknown';
        if (!convs[cid]) {
          convs[cid] = {
            conversation_id: cid,
            subject: msg.subject || '',
            with_agent: msg.from === agentId ? msg.to : msg.from,
            message_count: 0,
            unread: 0,
            last_message: msg.created,
            last_from: msg.from,
          };
        }
        convs[cid].message_count++;
        convs[cid].last_message = msg.created;
        convs[cid].last_from = msg.from;
        if (msg.direction !== 'sent' && (msg.status === 'pending' || msg.status === 'delivered')) {
          convs[cid].unread++;
        }
      } catch {}
    }

    const sorted = Object.values(convs).sort((a: any, b: any) =>
      (b.last_message || '').localeCompare(a.last_message || '')
    );
    res.json({ agent_id: agentId, conversations: sorted, total: sorted.length });
  } catch {
    res.json({ agent_id: agentId, conversations: [], total: 0 });
  }
});

// POST /api/messages/:msgId/reply — reply to a specific message (used by injected instructions)
router.post('/:msgId/reply', (req: Request, res: Response) => {
  const msgId = String(req.params.msgId);
  const { body, close } = req.body;
  if (!body) {
    res.status(400).json({ error: 'body required' });
    return;
  }
  const args = ['reply', '--message-id', msgId, '--body', body];
  if (close) args.push('--close');
  res.json(runBus(args));
});

// POST /api/messages/ack — acknowledge/mark as read
router.post('/ack', (req: Request, res: Response) => {
  const { message_id } = req.body;
  if (!message_id) {
    res.status(400).json({ error: 'message_id required' });
    return;
  }
  res.json(runBus(['ack', '--message-id', message_id]));
});

export default router;
