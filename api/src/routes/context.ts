/**
 * context.ts — GET /api/context/needs-attention.
 *
 * This endpoint never existed (404 on every load since 2026-08-19, see
 * dashboard/src/pages/Overview.tsx). It was deliberately left unfixed because
 * "what needs the operator" was already answered by three disagreeing stores
 * (approvals/unified, questionnaires, agent inbox/stall state) and inventing a
 * fourth independent answer would have made that worse, not better.
 *
 * This is NOT a fourth store: it federates the same three canonical endpoints
 * the Approvals and Agents pages already read (via an in-process loopback
 * call, so there is exactly one source of truth per item type), so this
 * summary and the pages it points to can never disagree.
 */
import { Router, type Request, type Response } from 'express';
import { loadConfig } from '../lib/config.js';

const router = Router();

function apiBase(): string {
  const port = process.env.PORT || process.env.ORCHESTRA_API_PORT || loadConfig().apiPort;
  return `http://127.0.0.1:${port}/api`;
}

async function safeFetchJson(url: string): Promise<any> {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

const STALE_STATES = new Set(['waiting', 'stranded', 'stalled', 'crashed']);

router.get('/needs-attention', async (_req: Request, res: Response) => {
  const base = apiBase();
  const items: Array<{ type: string; label: string }> = [];

  const [approvals, questionnaires, agents] = await Promise.all([
    safeFetchJson(`${base}/approvals/unified?status=pending`),
    safeFetchJson(`${base}/questionnaires`),
    safeFetchJson(`${base}/agents`),
  ]);

  for (const a of approvals?.items || []) {
    items.push({ type: 'approval', label: a.title || a.description || `${a.type || 'agent'} approval pending` });
  }
  for (const q of questionnaires?.questionnaires || []) {
    if (q.status === 'pending') {
      items.push({ type: 'approval', label: q.title || 'Questionnaire pending' });
    }
  }
  for (const a of agents?.agents || []) {
    const count = a.inbox_count || 0;
    if (count > 0) {
      items.push({
        type: 'messages',
        label: `${count} message${count === 1 ? '' : 's'} waiting for ${a.name || a.id}`,
      });
    }
    if (STALE_STATES.has(a.status)) {
      items.push({ type: 'stale', label: `${a.name || a.id} — ${a.activity || a.status}` });
    }
  }

  res.json({ items });
});

export default router;
