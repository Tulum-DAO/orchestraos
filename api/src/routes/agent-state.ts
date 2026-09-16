/**
 * agent-state.ts — Agent continuity: conversation logging, checkpoints, transitions.
 */

import { Router, type Request, type Response } from 'express';
import { execFileSync, execFile } from 'child_process';
import { join } from 'path';
import { existsSync, mkdirSync, writeFileSync, readFileSync } from 'fs';
import { queryDb, execDb } from '../lib/db.js';
import { loadConfig } from '../lib/config.js';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR || join(process.env.HOME!, 'scripts/agent-orchestra');

// ── Log conversation exchange ───────────────────────────────────

router.post('/:agentId/conversation', (req: Request, res: Response) => {
  const { role, content, metadata } = req.body;
  if (!role || !content) { res.status(400).json({ error: 'role and content required' }); return; }

  const agentId = String(req.params.agentId);

  // Get current generation
  const state = queryDb('SELECT generation FROM agent_work_state WHERE agent_id = ?', [agentId]);
  const generation = state.length ? state[0].generation : 1;

  execDb(`INSERT INTO agent_conversations (agent_id, generation, role, content, metadata, created_at) VALUES (?,?,?,?,?,?)`,
    [agentId, generation, role, content, metadata ? JSON.stringify(metadata) : null, new Date().toISOString()]);

  res.json({ logged: true, generation });
});

// ── Update work state checkpoint ────────────────────────────────

router.post('/:agentId/checkpoint', (req: Request, res: Response) => {
  const agentId = String(req.params.agentId);
  const { original_intent, current_task, progress, decisions, key_context, user_tone, next_action, tenant_id } = req.body;

  const existing = queryDb('SELECT * FROM agent_work_state WHERE agent_id = ?', [agentId]);
  const now = new Date().toISOString();

  if (existing.length) {
    const sets: string[] = ['updated_at = ?'];
    const params: any[] = [now];

    if (original_intent !== undefined) { sets.push('original_intent = ?'); params.push(original_intent); }
    if (current_task !== undefined) { sets.push('current_task = ?'); params.push(current_task); }
    if (progress !== undefined) { sets.push('progress = ?'); params.push(typeof progress === 'string' ? progress : JSON.stringify(progress)); }
    if (decisions !== undefined) { sets.push('decisions = ?'); params.push(typeof decisions === 'string' ? decisions : JSON.stringify(decisions)); }
    if (key_context !== undefined) { sets.push('key_context = ?'); params.push(typeof key_context === 'string' ? key_context : JSON.stringify(key_context)); }
    if (user_tone !== undefined) { sets.push('user_tone = ?'); params.push(user_tone); }
    if (next_action !== undefined) { sets.push('next_action = ?'); params.push(next_action); }

    params.push(agentId);
    execDb(`UPDATE agent_work_state SET ${sets.join(', ')} WHERE agent_id = ?`, params);
  } else {
    execDb(`INSERT INTO agent_work_state (agent_id, tenant_id, generation, original_intent, current_task, progress, decisions, key_context, user_tone, next_action, updated_at)
      VALUES (?,?,1,?,?,?,?,?,?,?,?)`,
      [agentId, tenant_id || loadConfig().operatorId, original_intent || null, current_task || null,
       progress ? JSON.stringify(progress) : null, decisions ? JSON.stringify(decisions) : null,
       key_context ? JSON.stringify(key_context) : null, user_tone || null, next_action || null, now]);
  }

  res.json({ checkpointed: true, agent_id: agentId });
});

// ── Get work state (for new instance to read) ───────────────────

router.get('/:agentId/state', (req: Request, res: Response) => {
  const agentId = String(req.params.agentId);

  const state = queryDb('SELECT * FROM agent_work_state WHERE agent_id = ?', [agentId]);
  if (!state.length) { res.status(404).json({ error: 'No work state' }); return; }

  const workState = state[0];

  // Parse JSON fields
  try { workState.progress = JSON.parse(workState.progress); } catch {}
  try { workState.decisions = JSON.parse(workState.decisions); } catch {}
  try { workState.key_context = JSON.parse(workState.key_context); } catch {}

  // Get recent conversation from current generation
  const recentConv = queryDb(
    'SELECT role, content, metadata, created_at FROM agent_conversations WHERE agent_id = ? AND generation = ? ORDER BY created_at DESC LIMIT 20',
    [agentId, workState.generation]
  );

  // Get summaries from all previous generations
  const summaries = queryDb(
    'SELECT generation, summary, decisions, files_changed, created_at FROM agent_conversation_summaries WHERE agent_id = ? ORDER BY generation DESC',
    [agentId]
  );

  res.json({
    work_state: workState,
    recent_conversation: recentConv.reverse(),  // chronological order
    previous_summaries: summaries,
  });
});

// ── Trigger transition (agent calls this when context is full) ──

router.post('/:agentId/transition', (req: Request, res: Response) => {
  const agentId = String(req.params.agentId);
  const { summary, decisions, files_changed } = req.body;

  // Get current generation
  const state = queryDb('SELECT generation FROM agent_work_state WHERE agent_id = ?', [agentId]);
  const currentGen = state.length ? state[0].generation : 1;

  // Save conversation summary for this generation
  if (summary) {
    execDb(`INSERT INTO agent_conversation_summaries (agent_id, generation, summary, decisions, files_changed, created_at) VALUES (?,?,?,?,?,?)`,
      [agentId, currentGen, summary,
       decisions ? JSON.stringify(decisions) : null,
       files_changed ? JSON.stringify(files_changed) : null,
       new Date().toISOString()]);
  }

  // Increment generation in work state
  execDb('UPDATE agent_work_state SET generation = generation + 1, updated_at = ? WHERE agent_id = ?',
    [new Date().toISOString(), agentId]);

  // Rename tmux session: {name} → {name}-transition
  try {
    execFileSync('tmux', ['rename-session', '-t', agentId, `${agentId}-transition`], { timeout: 3000 });
  } catch { /* may fail if session name differs from agent_id */ }

  // Spawn new instance (async — don't block the response)
  const spawnScript = join(ORCHESTRA, 'spawn-agent.sh');
  execFile('bash', [spawnScript, agentId], {
    env: { ...process.env, REINCARNATION: 'true', ORCHESTRA_DIR: ORCHESTRA },
    timeout: 60000,
  }, (err) => {
    if (err) console.error(`[agent-state] Reincarnation spawn failed for ${agentId}:`, err.message);
    else console.log(`[agent-state] Reincarnated ${agentId} → generation ${currentGen + 1}`);
  });

  // Notify via the configured channel (BYO Telegram bot; token never in code/config file)
  try {
    const cfg = loadConfig();
    if (cfg.notifyChannel === 'telegram' && cfg.telegramBotToken) {
      const msg = `\u{1F504} Agent ${agentId} transitioning (gen ${currentGen} → ${currentGen + 1})`;
      execFileSync('curl', ['-s', '-X', 'POST',
        `https://api.telegram.org/bot${cfg.telegramBotToken}/sendMessage`,
        '-d', `chat_id=${cfg.telegramChatId}`, '--data-urlencode', `text=${msg}`
      ], { timeout: 5000 });
    }
  } catch { /* non-critical */ }

  // Schedule cleanup of old instance (1 hour)
  setTimeout(() => {
    try {
      execFileSync('tmux', ['kill-session', '-t', `${agentId}-transition`], { timeout: 3000 });
      console.log(`[agent-state] Killed old transition session: ${agentId}-transition`);
    } catch { /* already dead */ }
  }, 3600000); // 1 hour

  res.json({
    transitioned: true,
    agent_id: agentId,
    old_generation: currentGen,
    new_generation: currentGen + 1,
  });
});

// ── Get conversation history for a generation ───────────────────

router.get('/:agentId/conversations', (req: Request, res: Response) => {
  const agentId = String(req.params.agentId);
  const generation = parseInt(req.query.generation as string) || undefined;
  const limit = Math.min(parseInt(req.query.limit as string) || 50, 200);

  let sql = 'SELECT * FROM agent_conversations WHERE agent_id = ?';
  const params: any[] = [agentId];

  if (generation) {
    sql += ' AND generation = ?';
    params.push(generation);
  }

  sql += ` ORDER BY created_at DESC LIMIT ${limit}`;

  const conversations = queryDb(sql, params);
  res.json({ conversations: conversations.reverse(), total: conversations.length });
});

// ── Live roster (survives crashes — the recovery source of truth) ──
router.get('/roster', (_req: Request, res: Response) => {
  const ORCHESTRA = process.env.ORCHESTRA_DIR || join(process.env.HOME!, 'scripts/agent-orchestra');
  const rosterPath = join(ORCHESTRA, 'state', 'live-roster.json');
  try {
    if (!existsSync(rosterPath)) { res.json({}); return; }
    const data = JSON.parse(readFileSync(rosterPath, 'utf-8'));
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// ── Recent-agents recency list (dashboard "recently viewed") ────
// File-backed {agents:[...]} store the /agents UI reads on load (GET) and
// writes when an agent is opened (PUT). Single-segment paths so they don't
// collide with the /:agentId/* routes above.

const recentAgentsPath = () =>
  join(process.env.ORCHESTRA_DIR || join(process.env.HOME!, 'scripts/agent-orchestra'),
    'state', 'recent-agents.json');

router.get('/recent-agents', (_req: Request, res: Response) => {
  try {
    const p = recentAgentsPath();
    if (!existsSync(p)) { res.json({ agents: [] }); return; }
    const data = JSON.parse(readFileSync(p, 'utf-8'));
    res.json({ agents: Array.isArray(data.agents) ? data.agents : [] });
  } catch {
    res.json({ agents: [] });
  }
});

router.put('/recent-agents', (req: Request, res: Response) => {
  const agents = req.body?.agents;
  if (!Array.isArray(agents)) { res.status(400).json({ error: 'agents array required' }); return; }
  try {
    const p = recentAgentsPath();
    mkdirSync(join(p, '..'), { recursive: true });
    // defensive cap so the recency list can't grow unbounded
    writeFileSync(p, JSON.stringify({ agents: agents.slice(0, 100) }, null, 2));
    res.json({ saved: true, count: Math.min(agents.length, 100) });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

export default router;
