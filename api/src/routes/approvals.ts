/**
 * approvals.ts — legacy /api/approvals route.
 *
 * SPEC all-model-parity R5: this route is a DUAL-READ SHIM during the web
 * convergence, MARKED FOR RETIREMENT (§2.3). Like unified-approvals it used to
 * own a filesystem store + write the deprecated queue/inbox/ side-channel;
 * both writes are retired. It now:
 *   - GET dual-READS: the canonical approval_requests pending feed UNION the
 *     frozen filesystem pending/ (canonical wins on op_key/id collision); the
 *     resolved/ archive is read-only.
 *   - approve/deny route the answer through the ONE canonical service
 *     (canonicalAnswer -> record_answer -> fire_resume), so a legacy-route
 *     answer resolves on watch/iOS too. No filesystem move, no inbox JSON, and
 *     the filesystem auto-approve policy learning retires with the store it fed
 *     on (it had no live input once the store froze).
 */
import { Router, type Request } from 'express';
import { readFileSync, existsSync, readdirSync } from 'fs';
import { join } from 'path';
import { readCanonicalPending, canonicalAnswer, type CanonicalRow } from './_canonical-approvals.js';
import { gateMenuRowsForClient } from './unified-approvals.js';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR!;
// Frozen filesystem store — dual-READ only (never written; retires at cutover).
const PENDING = join(ORCHESTRA, 'approvals', 'pending');
const RESOLVED = join(ORCHESTRA, 'approvals', 'resolved');

function readJSON(path: string) {
  try { return JSON.parse(readFileSync(path, 'utf-8')); } catch { return null; }
}

function readDir(dir: string) {
  if (!existsSync(dir)) return [];
  return readdirSync(dir).filter(f => f.endsWith('.json'));
}

/** Canonical row -> the raw pending shape this route's clients expect. */
function canonicalToLegacy(r: CanonicalRow) {
  return {
    id: r.id,
    agent_id: r.from_agent,
    action: r.question,
    question: r.question,
    created: r.created_at,
    type: r.kind || 'approval',
    status: r.status,
    op_key: r.op_key,
    options: r.options,
    source: 'canonical',
    // Additive pass-through (gm-mine-menu-card seam 1): kind/menu/options for menu rows
    kind: r.kind ?? null,
    menu: r.menu ?? null,
  };
}

router.get('/', (req: Request, res) => {
  // W5: gate BEFORE normalizing — a multipart && !walk_complete row never
  // reaches the client raw (see gateMenuRowsForClient in unified-approvals.ts).
  const canonical = gateMenuRowsForClient(readCanonicalPending(), req);
  const canonicalKeys = new Set<string>();
  for (const r of canonical) {
    canonicalKeys.add(r.id);
    if (r.op_key) canonicalKeys.add(r.op_key);
  }
  const canonicalPending = canonical.map(canonicalToLegacy);

  // Dual-read: filesystem residuals not already represented canonically.
  const fsPending = readDir(PENDING)
    .map(f => {
      const data = readJSON(join(PENDING, f));
      if (!data) return null;
      const fid = data.id || f.replace('.json', '');
      if (canonicalKeys.has(fid) || (data.op_key && canonicalKeys.has(data.op_key))) return null;
      return data;
    })
    .filter(Boolean);

  const pending = [...canonicalPending, ...fsPending]
    .sort((a: any, b: any) =>
      new Date(b.created || 0).getTime() - new Date(a.created || 0).getTime());

  const resolved = readDir(RESOLVED).map(f => readJSON(join(RESOLVED, f))).filter(Boolean)
    .sort((a: any, b: any) => new Date(b.resolved_at || 0).getTime() - new Date(a.resolved_at || 0).getTime())
    .slice(0, 20);

  res.json({ pending, resolved, pending_count: pending.length });
});

router.post('/:id/approve', (req, res) => {
  const id = String(req.params.id);
  const result = canonicalAnswer(id, 'approve');
  if (result.ok && result.applied) return res.json({ status: 'approved', id });
  if (result.ok && result.applied === false) return res.status(404).json({ error: 'Not found' });
  return res.status(400).json({ error: result.error || 'answer refused', id });
});

router.post('/:id/deny', (req, res) => {
  const id = String(req.params.id);
  const reason = typeof req.body?.reason === 'string' ? req.body.reason : undefined;
  const result = canonicalAnswer(id, 'deny', reason);
  if (result.ok && result.applied) return res.json({ status: 'denied', id });
  if (result.ok && result.applied === false) return res.status(404).json({ error: 'Not found' });
  return res.status(400).json({ error: result.error || 'answer refused', id });
});

export default router;
