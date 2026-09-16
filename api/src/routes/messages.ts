import { Router, type Request, type Response } from 'express';
import { execFileSync } from 'child_process';
import { readFileSync, existsSync } from 'fs';
import { join } from 'path';
import { loadConfig } from '../lib/config.js';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR || loadConfig().dataDir;
const MSG_STORE = join(ORCHESTRA, 'msg_store.py');
const BUS = existsSync(MSG_STORE) ? MSG_STORE : join(ORCHESTRA, 'message_bus.py');

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
