/**
 * learning.ts — API routes for the Learning Engine dashboard.
 * Reads from state/learning.db (SQLite via Python bridge).
 */

import { Router, type Request, type Response } from 'express';
import { execFileSync } from 'child_process';
import { join } from 'path';
import { existsSync, mkdirSync, readFileSync, writeFileSync as writeFeedbackFile } from 'fs';
import { loadConfig } from '../lib/config.js';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR || loadConfig().dataDir;
const DB_PATH = join(ORCHESTRA, 'state', 'learning.db');

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
    const result = execFileSync('python3', ['-c', script], { timeout: 5000, encoding: 'utf-8' });
    return JSON.parse(result.trim());
  } catch {
    return [];
  }
}

// GET /api/learning/snippets — recent scored snippets
router.get('/snippets', (req: Request, res: Response) => {
  const limit = parseInt(req.query.limit as string) || 50;
  const channel = req.query.channel as string;
  const minScore = parseFloat(req.query.min_score as string);
  const maxScore = parseFloat(req.query.max_score as string);

  let sql = 'SELECT * FROM snippets';
  const conditions: string[] = [];
  const params: string[] = [];

  if (channel) { conditions.push('channel = ?'); params.push(String(channel)); }
  if (!isNaN(minScore)) { conditions.push('score_composite >= ?'); params.push(String(minScore)); }
  if (!isNaN(maxScore)) { conditions.push('score_composite <= ?'); params.push(String(maxScore)); }

  if (conditions.length) sql += ' WHERE ' + conditions.join(' AND ');
  sql += ` ORDER BY ts DESC LIMIT ${limit}`;

  const snippets = queryDb(sql, params);
  res.json({ snippets, total: snippets.length });
});

// GET /api/learning/patterns — active detected patterns
router.get('/patterns', (_req: Request, res: Response) => {
  const patterns = queryDb(
    "SELECT * FROM patterns WHERE status = 'active' ORDER BY confidence DESC"
  );
  res.json({ patterns, total: patterns.length });
});

// GET /api/learning/proposals — all proposals
router.get('/proposals', (req: Request, res: Response) => {
  const statusFilter = req.query.status as string | undefined;
  let sql = 'SELECT proposal_id, pattern_id, type, status, description, mechanism, risk_level, tldr, qa_verdict, shaw_decision, created_at, updated_at FROM proposals';
  const params: string[] = [];
  if (statusFilter) {
    sql += ' WHERE status = ?';
    params.push(String(statusFilter));
  }
  sql += ' ORDER BY created_at DESC';
  const proposals = queryDb(sql, params);
  res.json({ proposals, total: proposals.length });
});

// GET /api/learning/proposals/:id — single proposal with one-pager
router.get('/proposals/:id', (req: Request, res: Response) => {
  const id = req.params.id as string;
  const proposals = queryDb(
    'SELECT * FROM proposals WHERE proposal_id = ?',
    [id]
  );
  if (!proposals.length) { res.status(404).json({ error: 'Proposal not found' }); return; }
  res.json(proposals[0]);
});

// GET /api/learning/proposals/:id/onepager — serve one-pager HTML
router.get('/proposals/:id/onepager', (req: Request, res: Response) => {
  const id = req.params.id as string;
  const proposals = queryDb(
    'SELECT onepager_html FROM proposals WHERE proposal_id = ?',
    [id]
  );
  if (!proposals.length || !proposals[0].onepager_html) {
    res.status(404).json({ error: 'One-pager not found' });
    return;
  }
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.send(proposals[0].onepager_html);
});

// POST /api/learning/proposals/:id/approve — the operator approves a proposal
router.post('/proposals/:id/approve', (req: Request, res: Response) => {
  try {
    const script = `
import sqlite3, json, sys
from datetime import datetime, timezone

db = sqlite3.connect("${DB_PATH}", timeout=5)
db.execute("PRAGMA journal_mode=WAL")
now = datetime.now(timezone.utc).isoformat()
proposal_id = "${req.params.id}"

# Get the proposal
p = db.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
if not p:
    print(json.dumps({"error": "not found"}))
    sys.exit(0)

# Update status
db.execute("UPDATE proposals SET status = 'approved', shaw_decision = 'approved', shaw_timestamp = ?, updated_at = ? WHERE proposal_id = ?",
    (now, now, proposal_id))

# Create active rule
rule_id = "rule_" + proposal_id.replace("prop_", "")
db.execute("""
    INSERT OR IGNORE INTO rules (rule_id, proposal_id, status, description, mechanism, rule_text, channel_scope, applied_at)
    VALUES (?, ?, 'probationary', ?, ?, ?, '*', ?)
""", (rule_id, proposal_id, p[4], p[7], p[8], now))  # description=p[4], mechanism=p[7], change_content=p[8]

db.execute("UPDATE proposals SET status = 'applied', applied_at = ? WHERE proposal_id = ?", (now, proposal_id))
db.commit()
db.close()
print(json.dumps({"approved": True, "rule_id": rule_id}))
`;
    const result = execFileSync('python3', ['-c', script], { timeout: 5000, encoding: 'utf-8' });
    res.json(JSON.parse(result.trim()));
  } catch (err: any) {
    res.status(500).json({ error: 'Failed to approve', detail: err.message });
  }
});

// POST /api/learning/proposals/:id/reject — the operator rejects a proposal
router.post('/proposals/:id/reject', (req: Request, res: Response) => {
  try {
    const script = `
import sqlite3, json
from datetime import datetime, timezone
db = sqlite3.connect("${DB_PATH}", timeout=5)
now = datetime.now(timezone.utc).isoformat()
db.execute("UPDATE proposals SET status = 'rejected', shaw_decision = 'rejected', shaw_timestamp = ?, updated_at = ? WHERE proposal_id = ?",
    (now, now, "${req.params.id}"))
db.commit()
db.close()
print(json.dumps({"rejected": True}))
`;
    const result = execFileSync('python3', ['-c', script], { timeout: 5000, encoding: 'utf-8' });
    res.json(JSON.parse(result.trim()));
  } catch (err: any) {
    res.status(500).json({ error: 'Failed to reject', detail: err.message });
  }
});

// GET /api/learning/rules — active learned rules
router.get('/rules', (_req: Request, res: Response) => {
  const rules = queryDb(
    "SELECT * FROM rules WHERE status IN ('probationary', 'permanent') ORDER BY applied_at DESC"
  );
  res.json({ rules, total: rules.length });
});

// GET /api/learning/stats — daily trends
router.get('/stats', (req: Request, res: Response) => {
  const days = parseInt(req.query.days as string) || 7;
  const stats = queryDb(
    `SELECT * FROM daily_stats ORDER BY date DESC LIMIT ${days * 3}`
  );

  // Also get current totals
  const totals = queryDb(`
    SELECT COUNT(*) as total_snippets,
           AVG(score_composite) as avg_composite,
           SUM(CASE WHEN outcome_status IN ('failure','error','timeout') THEN 1 ELSE 0 END) as total_failures,
           SUM(CASE WHEN feedback_signal = 'correction' THEN 1 ELSE 0 END) as total_corrections
    FROM snippets
  `);

  const patternCount = queryDb("SELECT COUNT(*) as count FROM patterns WHERE status = 'active'");
  const proposalCount = queryDb("SELECT COUNT(*) as count FROM proposals WHERE status IN ('pending_qa','pending_shaw')");
  const ruleCount = queryDb("SELECT COUNT(*) as count FROM rules WHERE status IN ('probationary','permanent')");

  res.json({
    daily: stats,
    totals: totals[0] || {},
    active_patterns: patternCount[0]?.count || 0,
    pending_proposals: proposalCount[0]?.count || 0,
    active_rules: ruleCount[0]?.count || 0,
  });
});

// POST /api/learning/feedback — receive questionnaire answers
router.post('/feedback', (req: Request, res: Response) => {
  try {
    const { questionnaire_id, answers, submitted_by, submitted_at } = req.body;
    if (!answers) { res.status(400).json({ error: 'answers required' }); return; }

    const feedbackDir = join(process.env.ORCHESTRA_DIR!, 'state', 'feedback');
    if (!existsSync(feedbackDir)) { mkdirSync(feedbackDir, { recursive: true }); }

    const filename = `${questionnaire_id || 'q'}_${Date.now()}.json`;
    writeFeedbackFile(
      join(feedbackDir, filename),
      JSON.stringify({ questionnaire_id, answers, submitted_by: submitted_by || loadConfig().operatorId, submitted_at: submitted_at || new Date().toISOString() }, null, 2)
    );

    // Auto-mark questionnaire as completed + notify creator
    if (questionnaire_id) {
      try {
        const indexFile = join(process.env.ORCHESTRA_DIR!, 'state', 'questionnaires', 'index.json');
        if (existsSync(indexFile)) {
          const index = JSON.parse(readFileSync(indexFile, 'utf-8'));
          const q = index.find((q: any) => q.id === questionnaire_id);
          if (q && q.status === 'pending') {
            q.status = 'completed';
            q.completed_at = submitted_at || new Date().toISOString();
            q.response_file = `state/feedback/${filename}`;
            writeFeedbackFile(indexFile, JSON.stringify(index, null, 2));

            // Notify the creating agent via inbox
            if (q.created_by && q.created_by !== 'system') {
              const inboxDir = join(process.env.ORCHESTRA_DIR!, 'queue', 'inbox', q.created_by);
              if (!existsSync(inboxDir)) { mkdirSync(inboxDir, { recursive: true }); }
              const notifyFile = join(inboxDir, `${Date.now()}_questionnaire_completed.json`);
              writeFeedbackFile(notifyFile, JSON.stringify({
                type: 'questionnaire_completed',
                questionnaire_id,
                title: q.title,
                submitted_by: submitted_by || loadConfig().operatorId,
                response_file: `state/feedback/${filename}`,
                timestamp: new Date().toISOString(),
              }, null, 2));
            }
          }
        }
      } catch { /* non-critical */ }
    }

    res.json({ received: true, filename });
  } catch (err: any) {
    res.status(500).json({ error: 'Failed to save feedback', detail: err.message });
  }
});

export default router;
