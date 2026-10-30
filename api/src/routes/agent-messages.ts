/**
 * GET /api/agents/:id/messages/:msgId — one message another agent (or a service) sent this agent, in full.
 *
 * An agent's chat holds only the line the router typed into its pane ("[MSG from <sender> | <priority>]
 * <subject> — Read /tmp/agent-msg-<id>.md …"); the body itself is in the message store. The chat draws that
 * line as "Message from <sender>" with the body under it, and reads the body here.
 *
 * Read-only. Scoped like the chat itself (lib/agent-scope): whoever can see this agent's chat can read the
 * messages it was sent, and only those: a message addressed to another agent is the same 404 as an unknown one.
 */
import { Router, type Request, type Response } from 'express';
import { existsSync } from 'fs';
import { join } from 'path';
import Database from 'better-sqlite3';
import { loadConfig } from '../lib/config.js';
import { agentScopeParam } from '../lib/agent-scope.js';

const router = Router();
router.param('id', agentScopeParam);

export interface AgentMessage {
  id: string; from_agent: string; to_agent: string; priority: string | null;
  subject: string | null; body: string | null; created_at: string | null;
  /** msg_store's message type (task, reply, status, phase_report, handoff, ...): a hand-back is drawn as a
   *  finished-work card. Additive. */
  type: string | null;
}

const MSG_ID = /^[A-Za-z0-9_]{1,80}$/;
/** A seat's generations share its messages ("dev-demo-g2" reads what "dev-demo" was sent). */
const base = (id: string) => id.replace(/-(?:gen|g)\d+$/, '');

export function readAgentMessage(dbPath: string, agentId: string, msgId: string): AgentMessage | null {
  if (!MSG_ID.test(msgId) || !existsSync(dbPath)) return null;
  try {
    const db = new Database(dbPath, { readonly: true, fileMustExist: true });
    try {
      const row = db.prepare(
        `select id, from_agent, to_agent, priority, subject, body, created_at, type from messages where id = ?`,
      ).get(msgId) as AgentMessage | undefined;
      return row && base(String(row.to_agent || '')) === base(agentId) ? row : null;
    } finally { db.close(); }
  } catch { return null; }
}

router.get('/:id/messages/:msgId', (req: Request, res: Response) => {
  const dataDir = process.env.ORCHESTRA_DIR || loadConfig().dataDir;
  const msg = readAgentMessage(join(dataDir, 'state', 'tasks.db'), String(req.params.id), String(req.params.msgId));
  if (!msg) { res.status(404).json({ error: 'Message not found' }); return; }
  res.json(msg);
});

export default router;
