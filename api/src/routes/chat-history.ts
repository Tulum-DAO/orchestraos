/**
 * chat-history.ts — Chat History API
 *
 * GET  /api/chat-history?view=projects|unified|operator&limit=50&offset=0
 * GET  /api/chat-history/thread/:conversationId
 * POST /api/chat-history/thread/:conversationId/reply
 */

import { Router, type Request, type Response } from 'express';
import { execFileSync } from 'child_process';
import { existsSync } from 'fs';
import { join } from 'path';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR || join(process.env.HOME!, 'scripts/agent-orchestra');
const DB_PATH = join(ORCHESTRA, 'state', 'tasks.db');
const BUS = join(ORCHESTRA, 'message_bus.py');

// ── SQLite helper ──────────────────────────────────────────────────────

function queryDb(sql: string, params: any[] = []): any[] {
  if (!existsSync(DB_PATH)) return [];
  try {
    const script = [
      'import sqlite3,json,sys',
      `conn=sqlite3.connect("""${DB_PATH}""",timeout=5)`,
      'conn.row_factory=sqlite3.Row',
      `rows=conn.execute("""${sql.replace(/"""/g, "'''")}""",json.loads(sys.argv[1])).fetchall()`,
      'conn.close()',
      'print(json.dumps([dict(r) for r in rows],default=str))',
    ].join('\n');
    const out = execFileSync('python3', ['-c', script, JSON.stringify(params)], {
      timeout: 8000,
      encoding: 'utf-8',
      cwd: ORCHESTRA,
    });
    return JSON.parse(out.trim());
  } catch {
    return [];
  }
}

// ── Project extraction ─────────────────────────────────────────────────

// Populate with your own project/client seat-id prefixes — this is a
// keyword-match example, not a fixed list.
const KNOWN_PROJECTS = [
  'orchestraos', 'acme', 'northwind',
];

function extractProject(agent: string): string {
  if (!agent) return 'general';
  const lower = agent.toLowerCase();
  for (const p of KNOWN_PROJECTS) {
    if (lower.includes(p.replace('-', ''))) return p;
    if (lower.includes(p)) return p;
  }
  // strip common suffixes: -dev, -gm, -pm, -web, -ui, etc.
  const stripped = lower
    .replace(/-dev$/, '')
    .replace(/-gm$/, '')
    .replace(/-pm$/, '')
    .replace(/-web$/, '')
    .replace(/-ui$/, '')
    .replace(/-ops$/, '')
    .replace(/-builder$/, '');
  return stripped || 'general';
}

function detectChannel(source: string, type: string): string {
  if (!source && !type) return 'dashboard';
  const s = (source || '').toLowerCase();
  const t = (type || '').toLowerCase();
  if (s.includes('telegram') || t.includes('telegram')) return 'telegram';
  if (s.includes('voice') || t.includes('voice')) return 'voice';
  if (s.includes('agent') || t.includes('agent') || t.includes('route') || t.includes('brief')) return 'agent';
  return 'dashboard';
}

// ── GET /api/chat-history ──────────────────────────────────────────────

router.get('/', (req: Request, res: Response) => {
  const view = (req.query.view as string) || 'unified';
  const limit = Math.min(Number(req.query.limit) || 50, 200);
  const offset = Number(req.query.offset) || 0;

  if (view === 'operator') {
    const rows = queryDb(
      `SELECT m.id, m.from_agent, m.to_agent, m.type, m.subject, m.body,
              m.status, m.source, m.conversation_id, m.created_at, m.tenant_id
       FROM messages m
       WHERE m.from_agent = 'operator' OR m.to_agent = 'operator'
       ORDER BY m.created_at DESC
       LIMIT ? OFFSET ?`,
      [limit, offset]
    );
    const messages = rows.map(r => ({
      ...r,
      from: r.from_agent,
      to: r.to_agent,
      channel: detectChannel(r.source, r.type),
    }));
    res.json({ view: 'operator', messages, total: messages.length });
    return;
  }

  if (view === 'projects') {
    const rows = queryDb(
      `SELECT c.id, c.subject, c.participants, c.mode, c.status, c.tenant_id,
              c.iteration_count, c.created_at, c.updated_at,
              m.from_agent, m.to_agent, m.type as msg_type, m.body as last_body,
              m.source, m.created_at as last_msg_at
       FROM conversations c
       LEFT JOIN messages m ON m.id = (
         SELECT id FROM messages WHERE conversation_id = c.id
         ORDER BY created_at DESC LIMIT 1
       )
       ORDER BY COALESCE(c.updated_at, c.created_at) DESC
       LIMIT ? OFFSET ?`,
      [limit, offset]
    );

    const convs = rows.map(r => ({
      ...r,
      participants: (() => {
        try { return JSON.parse(r.participants || '[]'); } catch { return []; }
      })(),
      channel: detectChannel(r.source, r.msg_type),
      project: extractProject(r.from_agent || r.to_agent || ''),
    }));

    const projectMap = new Map<string, any[]>();
    for (const c of convs) {
      const p = c.project;
      if (!projectMap.has(p)) projectMap.set(p, []);
      projectMap.get(p)!.push(c);
    }

    const groups = Array.from(projectMap.entries())
      .map(([project, conversations]) => ({ project, conversations }))
      .sort((a, b) => {
        const aTime = a.conversations[0]?.last_msg_at || a.conversations[0]?.updated_at || '';
        const bTime = b.conversations[0]?.last_msg_at || b.conversations[0]?.updated_at || '';
        return bTime.localeCompare(aTime);
      });

    res.json({ view: 'projects', groups, total: convs.length });
    return;
  }

  // unified — conversations newest first
  const rows = queryDb(
    `SELECT c.id, c.subject, c.participants, c.mode, c.status, c.tenant_id,
            c.iteration_count, c.created_at, c.updated_at,
            m.from_agent, m.to_agent, m.type as msg_type, m.body as last_body,
            m.source, m.created_at as last_msg_at
     FROM conversations c
     LEFT JOIN messages m ON m.id = (
       SELECT id FROM messages WHERE conversation_id = c.id
       ORDER BY created_at DESC LIMIT 1
     )
     ORDER BY COALESCE(c.updated_at, c.created_at) DESC
     LIMIT ? OFFSET ?`,
    [limit, offset]
  );

  const conversations = rows.map(r => ({
    ...r,
    participants: (() => {
      try { return JSON.parse(r.participants || '[]'); } catch { return []; }
    })(),
    channel: detectChannel(r.source, r.msg_type),
  }));

  res.json({ view: 'unified', conversations, total: conversations.length });
});

// ── GET /api/chat-history/thread/:conversationId ───────────────────────

router.get('/thread/:conversationId', (req: Request, res: Response) => {
  const { conversationId } = req.params;

  const rows = queryDb(
    `SELECT id, from_agent, to_agent, type, subject, body, status, source, created_at
     FROM messages
     WHERE conversation_id = ?
     ORDER BY created_at ASC
     LIMIT 200`,
    [conversationId]
  );

  const messages = rows.map(r => ({
    ...r,
    channel: detectChannel(r.source, r.type),
  }));

  const convRows = queryDb(
    `SELECT id, subject, participants, status, mode, iteration_count, created_at, updated_at, tenant_id
     FROM conversations WHERE id = ? LIMIT 1`,
    [conversationId]
  );
  const conv = convRows[0] || null;

  res.json({
    conversation_id: conversationId,
    conversation: conv,
    messages,
    count: messages.length,
  });
});

// ── POST /api/chat-history/thread/:conversationId/reply ───────────────

router.post('/thread/:conversationId/reply', (req: Request, res: Response) => {
  const { conversationId } = req.params;
  const { body, from = 'operator', to, subject } = req.body;

  if (!body?.trim()) {
    res.status(400).json({ error: 'body required' });
    return;
  }

  let recipient = to;
  if (!recipient) {
    const rows = queryDb(
      `SELECT from_agent, to_agent FROM messages WHERE conversation_id = ? ORDER BY created_at ASC LIMIT 1`,
      [conversationId]
    );
    if (rows.length > 0) {
      const first = rows[0];
      recipient = first.from_agent !== from ? first.from_agent : first.to_agent;
    }
    recipient = recipient || 'gm';
  }

  try {
    const args = [
      BUS, 'send',
      '--from', from,
      '--to', recipient,
      '--subject', subject || 'Reply',
      '--body', body.trim(),
      '--conversation-id', conversationId,
    ];
    const out = execFileSync('python3', args, {
      encoding: 'utf-8',
      timeout: 10000,
      cwd: ORCHESTRA,
    });
    try {
      res.json(JSON.parse(out));
    } catch {
      res.json({ status: 'sent', raw: out.trim() });
    }
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

export default router;
