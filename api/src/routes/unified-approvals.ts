/**
 * unified-approvals.ts — Unified approvals API that merges learning proposals
 * and agent approvals into a single queryable endpoint.
 *
 * SPEC all-model-parity R5 (web convergence): the agent-approval lane now reads
 * and writes the CANONICAL `approval_requests` store (state/tasks.db) via the
 * one approval service (scripts/approval.py), the SAME ledger iOS + watch use.
 * The pre-convergence behaviour (read state/approvals/pending, write a
 * filesystem move + a deprecated queue/inbox/ JSON) is retired:
 *   - GET dual-READS during transition (§2.3): the canonical pending feed
 *     UNION the frozen filesystem pending/ (canonical wins on op_key/id
 *     collision). Writes go canonical-only — no dual-write.
 *   - approve/deny route the answer through canonicalAnswer() -> the canonical
 *     record_answer -> fire_resume path, so a web answer resolves on watch/iOS
 *     too (split-brain closed). No more queue/inbox side-channel.
 * Learning proposals (prop_*) keep their learning.db lifecycle unchanged.
 */

import { Router, type Request, type Response } from 'express';
import { execFileSync } from 'child_process';
import { join } from 'path';
import { existsSync, readFileSync, readdirSync } from 'fs';
import { loadConfig } from '../lib/config.js';
import {
  readCanonicalPending,
  canonicalAnswer,
  type CanonicalRow,
} from './_canonical-approvals.js';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR || loadConfig().dataDir;
const DB_PATH = join(ORCHESTRA, 'state', 'learning.db');
// Frozen filesystem store — dual-READ only (retires when pending/ drains, §2.1).
const AGENT_PENDING = join(ORCHESTRA, 'state', 'approvals', 'pending');
const AGENT_RESOLVED = join(ORCHESTRA, 'state', 'approvals', 'resolved');

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function queryDb(sql: string, params: string[] = []): any[] {
  if (!existsSync(DB_PATH)) return [];
  try {
    const script = `
import sqlite3, json, sys
conn = sqlite3.connect("${DB_PATH}", timeout=2)
conn.row_factory = sqlite3.Row
rows = conn.execute("""${sql.replace(/"/g, '\\"')}""", ${JSON.stringify(params)}).fetchall()
conn.close()
print(json.dumps([dict(r) for r in rows], default=str))
`;
    const result = execFileSync('python3', ['-c', script], {
      timeout: 5000,
      encoding: 'utf-8',
    });
    return JSON.parse(result.trim());
  } catch {
    return [];
  }
}

function execDb(script: string): any {
  try {
    const result = execFileSync('python3', ['-c', script], {
      timeout: 5000,
      encoding: 'utf-8',
    });
    return JSON.parse(result.trim());
  } catch (err: any) {
    return { error: err.message };
  }
}

function readJSON(path: string): any | null {
  try {
    return JSON.parse(readFileSync(path, 'utf-8'));
  } catch {
    return null;
  }
}

function readDir(dir: string): string[] {
  if (!existsSync(dir)) return [];
  return readdirSync(dir).filter((f) => f.endsWith('.json'));
}

// ---------------------------------------------------------------------------
// W5 capability gate (DOCS/SURFACE_CONTRACTS.md §1.1b) — TS-side equivalent
// of scripts/watch_gateway.py's _client_hydrates_multipart /
// _failsafe_unhydrated_multipart / gate_menu_rows_for_client. EVERY endpoint
// that returns an approval/menu ROW to a client MUST apply this gate: a
// multipart && !walk_complete row served raw to a non-capable client is an
// answerable broken card. This route joins /pending-approvals + /approvals/
// {id} (watch_gateway) on the W5 known-gated-egress list (DOCS/
// SURFACE_CONTRACTS.md).
// ---------------------------------------------------------------------------

export function clientHydratesMultipart(req: Request): boolean {
  const cap = (req.headers['x-client-capabilities'] as string) || '';
  const toks = new Set(
    cap.split(/[,\s]+/).map((t) => t.trim().toLowerCase()).filter(Boolean)
  );
  return toks.has('hydrates-multipart');
}

export function failsafeUnhydratedMultipart(row: CanonicalRow, capable: boolean): CanonicalRow {
  const menu = row.menu;
  if (!menu || typeof menu !== 'object') return row;
  if (!(menu.multipart && !menu.walk_complete)) return row; // single-part / hydrated
  if (capable) return row; // capable client hydrates it itself
  return {
    ...row,
    menu: {
      ...menu,
      read_only: true,
      fail_safe: true,
      options: [],
      notice: 'Multi-part menu — open the agent to answer.',
    },
    options: [],
  };
}

export function gateMenuRowsForClient(rows: CanonicalRow[], req: Request): CanonicalRow[] {
  const capable = clientHydratesMultipart(req);
  return rows.map((r) => failsafeUnhydratedMultipart(r, capable));
}

// ---------------------------------------------------------------------------
// Normalizers — transform source-specific shapes into the unified schema
// ---------------------------------------------------------------------------

export interface UnifiedApproval {
  id: string;
  type: 'learning' | 'agent' | 'deployment';
  urgency: 'low' | 'normal' | 'critical';
  status: 'pending' | 'approved' | 'rejected';
  title: string;
  description: string;
  risk_level: 'low' | 'medium' | 'high';
  evidence_summary: string;
  qa_verdict: string | null;
  sandbox_result: { passed: boolean; total: number; failed: string[] } | null;
  onepager_url: string | null;
  created_at: string;
  updated_at: string;
  source: 'learning_engine' | 'agent_request' | 'deployment_gate';
  // gm-mine-menu-card seam 1: additive pass-through of the canonical row's
  // kind/menu/options so a kind='menu' row can render its options on the web
  // dashboard (Approvals.tsx / Inbox.tsx). None/undefined on every other row
  // — no existing field above changes shape or meaning.
  kind?: string | null;
  menu?: CanonicalRow['menu'];
  options?: string[] | null;
}

function normalizeProposal(p: any): UnifiedApproval {
  // Map learning engine statuses to unified statuses
  let status: UnifiedApproval['status'] = 'pending';
  if (['approved', 'applied'].includes(p.status)) status = 'approved';
  else if (p.status === 'rejected') status = 'rejected';

  // Map risk_level to our enum
  let riskLevel: UnifiedApproval['risk_level'] = 'medium';
  if (p.risk_level === 'low') riskLevel = 'low';
  else if (p.risk_level === 'high') riskLevel = 'high';

  // Determine urgency from risk
  let urgency: UnifiedApproval['urgency'] = 'normal';
  if (riskLevel === 'high') urgency = 'critical';
  else if (riskLevel === 'low') urgency = 'low';

  // Parse sandbox_result if stored as JSON string
  let sandboxResult: UnifiedApproval['sandbox_result'] = null;
  if (p.sandbox_result) {
    try {
      sandboxResult =
        typeof p.sandbox_result === 'string'
          ? JSON.parse(p.sandbox_result)
          : p.sandbox_result;
    } catch {
      /* ignore parse errors */
    }
  }

  // Human-readable mechanism labels
  const mechanismLabels: Record<string, string> = {
    prompt_injection: 'Updates Jarvis behavior',
    code_guardrail: 'Adds code-level guardrail',
    tool_routing: 'Changes tool routing',
    rate_limit: 'Adjusts rate limiting',
    fallback_chain: 'Updates fallback behavior',
    parameter_default: 'Changes default setting',
  };

  // Parse evidence for inline display
  let evidenceText = '';
  let patternType = '';
  try {
    const ev = typeof p.evidence === 'string' ? JSON.parse(p.evidence) : (p.evidence || {});
    const corrections = ev.corrections || [];
    if (corrections.length > 0) {
      evidenceText = `Based on ${ev.sample_size || corrections.length} correction(s): "${corrections[0]}"`;
      patternType = 'correction';
    } else if (ev.failure_rate) {
      evidenceText = `Fails ${Math.round(ev.failure_rate * 100)}% of the time across ${ev.sample_size || '?'} interactions`;
      patternType = 'failure';
    } else if (ev.sample_size) {
      evidenceText = `Detected from ${ev.sample_size} interactions (confidence: ${ev.confidence || '?'})`;
    }
  } catch { /* ignore */ }

  return {
    id: p.proposal_id,
    type: 'learning' as const,
    urgency,
    status,
    title: p.tldr || p.description?.slice(0, 80) || 'Learning Proposal',
    description: p.description || '',
    risk_level: riskLevel,
    evidence_summary: evidenceText || mechanismLabels[p.mechanism] || p.mechanism || '',
    qa_verdict: p.qa_verdict || null,
    sandbox_result: sandboxResult,
    onepager_url: `/api/learning/proposals/${p.proposal_id}/onepager`,
    created_at: p.created_at || '',
    updated_at: p.updated_at || p.created_at || '',
    source: 'learning_engine',
    // Extra fields for rich card
    mechanism_label: mechanismLabels[p.mechanism] || p.mechanism || '',
    rule_text: p.change_content || '',
    pattern_type: patternType || p.type || '',
    initiated_by: patternType === 'correction' ? 'operator' : 'system',
  } as any;
}

/**
 * A canonical `approval_requests` row -> the unified card. The canonical
 * pending feed only carries status='pending' rows, so these always render as
 * pending; an answer transitions the ledger row and it drops from the next
 * poll (resolve-everywhere). Identity fields (provider/seat_id/...) are
 * provenance only and never change the card shape.
 */
export function normalizeCanonical(r: CanonicalRow): UnifiedApproval {
  let riskLevel: UnifiedApproval['risk_level'] = 'medium';
  if (r.risk_level === 'low') riskLevel = 'low';
  else if (r.risk_level === 'high') riskLevel = 'high';

  let urgency: UnifiedApproval['urgency'] = 'normal';
  if (riskLevel === 'high') urgency = 'critical';
  else if (riskLevel === 'low') urgency = 'low';

  return {
    id: r.id,
    type: 'agent',
    urgency,
    status: 'pending',
    title: r.question || `Agent request from ${r.from_agent || 'unknown'}`,
    description: r.summary || r.question || '',
    risk_level: riskLevel,
    evidence_summary: '',
    qa_verdict: null,
    sandbox_result: null,
    onepager_url: null,
    created_at: r.created_at || '',
    updated_at: r.created_at || '',
    source: 'agent_request',
    // Additive pass-through (gm-mine-menu-card seam 1) — see UnifiedApproval.
    kind: r.kind ?? null,
    menu: r.menu ?? null,
    options: r.options ?? null,
  };
}

function normalizeAgentApproval(a: any, filename: string): UnifiedApproval {
  const id = a.id || filename.replace('.json', '');

  // Map agent approval statuses
  let status: UnifiedApproval['status'] = 'pending';
  if (a.status === 'approved') status = 'approved';
  else if (['denied', 'rejected'].includes(a.status)) status = 'rejected';

  // Determine urgency from priority or category
  let urgency: UnifiedApproval['urgency'] = 'normal';
  if (a.urgency === 'critical' || a.priority === 'critical') urgency = 'critical';
  else if (a.urgency === 'low' || a.priority === 'low') urgency = 'low';

  // Risk level from category or explicit field
  let riskLevel: UnifiedApproval['risk_level'] = 'medium';
  if (a.risk_level === 'low' || a.risk === 'low') riskLevel = 'low';
  else if (a.risk_level === 'high' || a.risk === 'high') riskLevel = 'high';

  return {
    id,
    type: a.type === 'deployment' ? 'deployment' : 'agent',
    urgency,
    status,
    title: a.title || a.action?.slice(0, 80) || `Agent request from ${a.agent_id || 'unknown'}`,
    description: a.description || a.action || a.reason || '',
    risk_level: riskLevel,
    evidence_summary: a.context || a.evidence || '',
    qa_verdict: a.qa_verdict || null,
    sandbox_result: null,
    onepager_url: null,
    created_at: a.created || a.created_at || a.timestamp || '',
    updated_at: a.resolved_at || a.updated_at || a.created || '',
    source: a.type === 'deployment' ? 'deployment_gate' : 'agent_request',
  };
}

// ---------------------------------------------------------------------------
// GET /api/approvals/unified — list all approval items
// ---------------------------------------------------------------------------

router.get('/', (req: Request, res: Response) => {
  const typeFilter = (req.query.type as string) || 'all';
  const urgencyFilter = req.query.urgency as string | undefined;
  const statusFilter = req.query.status as string | undefined;

  const items: UnifiedApproval[] = [];

  // --- Learning proposals ---
  if (typeFilter === 'all' || typeFilter === 'learning') {
    let sql =
      'SELECT proposal_id, pattern_id, type, status, description, mechanism, risk_level, tldr, qa_verdict, sandbox_result, shaw_decision, created_at, updated_at, change_content, evidence FROM proposals';
    const conditions: string[] = [];
    const params: string[] = [];

    if (statusFilter === 'pending') {
      conditions.push("status IN ('pending_qa', 'pending_shaw')");
    } else if (statusFilter === 'resolved') {
      conditions.push("status IN ('approved', 'applied', 'rejected')");
    }

    if (conditions.length) sql += ' WHERE ' + conditions.join(' AND ');
    sql += ' ORDER BY created_at DESC';

    const proposals = queryDb(sql, params);
    for (const p of proposals) {
      const normalized = normalizeProposal(p);
      if (urgencyFilter && normalized.urgency !== urgencyFilter) continue;
      items.push(normalized);
    }
  }

  // --- Agent approvals (canonical approval_requests + frozen filesystem) ---
  if (typeFilter === 'all' || typeFilter === 'agent') {
    // Pending: canonical feed UNION filesystem residuals (§2.3 dual-read).
    if (!statusFilter || statusFilter === 'pending') {
      // W5: gate BEFORE normalizing — a multipart && !walk_complete row never
      // reaches the client raw (see gateMenuRowsForClient above).
      const canonical = gateMenuRowsForClient(readCanonicalPending(), req);
      // canonical WINS on collision — index its ids AND op_keys.
      const canonicalKeys = new Set<string>();
      for (const r of canonical) {
        canonicalKeys.add(r.id);
        if (r.op_key) canonicalKeys.add(r.op_key);
        const normalized = normalizeCanonical(r);
        if (urgencyFilter && normalized.urgency !== urgencyFilter) continue;
        items.push(normalized);
      }
      for (const f of readDir(AGENT_PENDING)) {
        const data = readJSON(join(AGENT_PENDING, f));
        if (!data) continue;
        const fid = data.id || f.replace('.json', '');
        // Skip a filesystem row already represented canonically (post-backfill).
        if (canonicalKeys.has(fid) || (data.op_key && canonicalKeys.has(data.op_key))) continue;
        const normalized = normalizeAgentApproval(data, f);
        if (urgencyFilter && normalized.urgency !== urgencyFilter) continue;
        items.push(normalized);
      }
    }

    // Resolved: the read-only filesystem archive (§2.1 — retained, never written).
    if (!statusFilter || statusFilter === 'resolved') {
      for (const f of readDir(AGENT_RESOLVED)) {
        const data = readJSON(join(AGENT_RESOLVED, f));
        if (!data) continue;
        const normalized = normalizeAgentApproval(data, f);
        if (urgencyFilter && normalized.urgency !== urgencyFilter) continue;
        items.push(normalized);
      }
    }
  }

  // Sort all items by created_at descending
  items.sort(
    (a, b) =>
      new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime()
  );

  res.json({ items, total: items.length });
});

// ---------------------------------------------------------------------------
// GET /api/approvals/unified/stats — counts by type and urgency
// ---------------------------------------------------------------------------

router.get('/stats', (_req: Request, res: Response) => {
  const stats = {
    by_type: { learning: 0, agent: 0, deployment: 0 },
    by_urgency: { low: 0, normal: 0, critical: 0 },
    by_status: { pending: 0, approved: 0, rejected: 0 },
    total: 0,
  };

  // Learning proposals pending
  const proposalStats = queryDb(`
    SELECT
      COUNT(*) as total,
      SUM(CASE WHEN status IN ('pending_qa','pending_shaw') THEN 1 ELSE 0 END) as pending,
      SUM(CASE WHEN status IN ('approved','applied') THEN 1 ELSE 0 END) as approved,
      SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected,
      SUM(CASE WHEN risk_level = 'high' THEN 1 ELSE 0 END) as high_risk,
      SUM(CASE WHEN risk_level = 'low' THEN 1 ELSE 0 END) as low_risk
    FROM proposals
  `);

  if (proposalStats.length) {
    const ps = proposalStats[0];
    stats.by_type.learning = ps.total || 0;
    stats.by_status.pending += ps.pending || 0;
    stats.by_status.approved += ps.approved || 0;
    stats.by_status.rejected += ps.rejected || 0;
    stats.by_urgency.critical += ps.high_risk || 0;
    stats.by_urgency.low += ps.low_risk || 0;
    stats.by_urgency.normal +=
      (ps.total || 0) - (ps.high_risk || 0) - (ps.low_risk || 0);
  }

  // Agent approvals: canonical pending UNION filesystem residuals + archive.
  const canonical = readCanonicalPending();
  const canonicalKeys = new Set<string>();
  for (const r of canonical) {
    canonicalKeys.add(r.id);
    if (r.op_key) canonicalKeys.add(r.op_key);
    stats.by_type.agent += 1;
    stats.by_status.pending += 1;
    if (r.risk_level === 'high') stats.by_urgency.critical += 1;
    else if (r.risk_level === 'low') stats.by_urgency.low += 1;
    else stats.by_urgency.normal += 1;
  }

  const pendingFiles = readDir(AGENT_PENDING);
  const resolvedFiles = readDir(AGENT_RESOLVED);

  for (const f of pendingFiles) {
    const data = readJSON(join(AGENT_PENDING, f));
    if (!data) continue;
    const fid = data.id || f.replace('.json', '');
    if (canonicalKeys.has(fid) || (data.op_key && canonicalKeys.has(data.op_key))) continue;
    stats.by_type.agent += 1;
    stats.by_status.pending += 1;
    const u = data.urgency || data.priority || 'normal';
    if (u === 'critical') stats.by_urgency.critical++;
    else if (u === 'low') stats.by_urgency.low++;
    else stats.by_urgency.normal++;
  }

  for (const f of resolvedFiles) {
    const data = readJSON(join(AGENT_RESOLVED, f));
    if (!data) continue;
    stats.by_type.agent += 1;
    if (data.status === 'approved') stats.by_status.approved++;
    else stats.by_status.rejected++;
  }

  stats.total =
    stats.by_type.learning + stats.by_type.agent + stats.by_type.deployment;

  res.json(stats);
});

// ---------------------------------------------------------------------------
// POST /api/approvals/unified/:id/approve — approve any item
// ---------------------------------------------------------------------------

router.post('/:id/approve', (req: Request, res: Response) => {
  const id = String(req.params.id);
  const reason = req.body?.reason || '';

  // Try learning proposal first (learning.db lifecycle — unchanged, §2.2).
  const proposals = queryDb(
    'SELECT proposal_id, status FROM proposals WHERE proposal_id = ?',
    [id]
  );

  if (proposals.length) {
    // Learning proposal — approve via Python bridge. Idempotent on the answer
    // side (INSERT OR IGNORE the rule; a double-approve applies the rule once).
    const result = execDb(
      `import sqlite3, json, sys\nfrom datetime import datetime, timezone\n` +
        `db = sqlite3.connect("${DB_PATH}", timeout=5)\n` +
        `db.execute("PRAGMA journal_mode=WAL")\n` +
        `now = datetime.now(timezone.utc).isoformat()\n` +
        `proposal_id = "${id}"\n` +
        `reason = """${(reason || '').replace(/"/g, '\\"')}"""\n` +
        `p = db.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()\n` +
        `if not p:\n` +
        `    print(json.dumps({"error": "not found"}))\n` +
        `    sys.exit(0)\n` +
        `db.execute("UPDATE proposals SET status = 'approved', shaw_decision = 'approved', shaw_timestamp = ?, updated_at = ? WHERE proposal_id = ?", (now, now, proposal_id))\n` +
        `rule_id = "rule_" + proposal_id.replace("prop_", "")\n` +
        `db.execute("INSERT OR IGNORE INTO rules (rule_id, proposal_id, status, description, mechanism, rule_text, channel_scope, applied_at) VALUES (?, ?, 'probationary', ?, ?, ?, '*', ?)", (rule_id, proposal_id, p[4] if len(p) > 4 else '', p[7] if len(p) > 7 else '', p[8] if len(p) > 8 else '', now))\n` +
        `db.execute("UPDATE proposals SET status = 'applied', applied_at = ? WHERE proposal_id = ?", (now, proposal_id))\n` +
        `db.commit()\n` +
        `db.close()\n` +
        `print(json.dumps({"approved": True, "rule_id": rule_id, "id": proposal_id}))`
    );

    if (result?.error && result.error !== 'not found') {
      res.status(500).json({ error: 'Failed to approve learning proposal', detail: result.error });
      return;
    }
    res.json({ status: 'approved', type: 'learning', ...result });
    return;
  }

  // Agent approval -> CANONICAL answer (record_answer -> fire_resume). No more
  // filesystem move, no more queue/inbox side-channel; watch/iOS resolve too.
  const result = canonicalAnswer(id, 'approve');
  if (result.ok && result.applied) {
    res.json({ status: 'approved', type: 'agent', id });
    return;
  }
  if (result.ok && result.applied === false) {
    // Unknown/already-answered canonical id, and not a proposal. A residual
    // filesystem-only row can't be answered canonically until the R7 backfill.
    res.status(404).json({ error: `Approval item '${id}' not found in the canonical store` });
    return;
  }
  res.status(400).json({ error: result.error || 'answer refused', id });
});

// ---------------------------------------------------------------------------
// POST /api/approvals/unified/:id/deny — deny any item (reason required)
// ---------------------------------------------------------------------------

router.post('/:id/deny', (req: Request, res: Response) => {
  const id = String(req.params.id);
  const reason = req.body?.reason;

  if (!reason || typeof reason !== 'string' || !reason.trim()) {
    res.status(400).json({ error: 'reason is required when denying an approval' });
    return;
  }

  // Try learning proposal first (learning.db lifecycle — unchanged).
  const proposals = queryDb(
    'SELECT proposal_id, status FROM proposals WHERE proposal_id = ?',
    [id]
  );

  if (proposals.length) {
    const result = execDb(
      `import sqlite3, json\n` +
        `from datetime import datetime, timezone\n` +
        `db = sqlite3.connect("${DB_PATH}", timeout=5)\n` +
        `db.execute("PRAGMA journal_mode=WAL")\n` +
        `now = datetime.now(timezone.utc).isoformat()\n` +
        `proposal_id = "${id}"\n` +
        `reason = """${reason.replace(/"/g, '\\"')}"""\n` +
        `db.execute("UPDATE proposals SET status = 'rejected', shaw_decision = 'rejected', shaw_timestamp = ?, rejection_reason = ?, updated_at = ? WHERE proposal_id = ?", (now, reason, now, proposal_id))\n` +
        `db.commit()\n` +
        `db.close()\n` +
        `print(json.dumps({"rejected": True, "id": proposal_id}))`
    );

    if (result?.error) {
      res.status(500).json({ error: 'Failed to deny learning proposal', detail: result.error });
      return;
    }
    res.json({ status: 'rejected', type: 'learning', reason, ...result });
    return;
  }

  // Agent approval -> CANONICAL answer, carrying the reason as the note text.
  const result = canonicalAnswer(id, 'deny', reason);
  if (result.ok && result.applied) {
    res.json({ status: 'rejected', type: 'agent', id, reason });
    return;
  }
  if (result.ok && result.applied === false) {
    res.status(404).json({ error: `Approval item '${id}' not found in the canonical store` });
    return;
  }
  res.status(400).json({ error: result.error || 'answer refused', id });
});

// ---------------------------------------------------------------------------
// POST /api/approvals/unified/:id/answer — kind='menu' option/free-text
// answer (V3: options-are-actions + universal Respond). Routes through the
// SAME canonicalAnswer() -> approval.py answer -> watch_gateway.apply_answer
// state machine approve/deny already use; no new answer/validation logic is
// introduced here (A1/A2/A3 are enforced entirely in apply_answer). A1: valid
// if EITHER option_n or answer_text is present; A2: both may be sent
// together; A3 is a CLIENT-side (React) merge-preserve concern, not this
// route's — it forwards whatever the client submits.
// ---------------------------------------------------------------------------

router.post('/:id/answer', (req: Request, res: Response) => {
  const id = String(req.params.id);
  const optionN = req.body?.option_n != null ? String(req.body.option_n) : undefined;
  const answerText = typeof req.body?.answer_text === 'string' ? req.body.answer_text : undefined;

  if (!optionN && !answerText) {
    res.status(400).json({ error: 'option_n or answer_text is required' }); // A1
    return;
  }

  const result = canonicalAnswer(id, 'option', undefined, { optionN, answerText });
  if (result.ok && result.applied) {
    res.json({ status: 'answered', type: 'agent', id, option_n: optionN, answer_text: answerText });
    return;
  }
  if (result.ok && result.applied === false) {
    res.status(404).json({ error: `Approval item '${id}' not found in the canonical store` });
    return;
  }
  res.status(400).json({ error: result.error || 'answer refused', id });
});

export default router;
