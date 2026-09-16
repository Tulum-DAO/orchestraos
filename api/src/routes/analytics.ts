import { Router, type Request, type Response } from 'express';
import { readFileSync, existsSync, readdirSync } from 'fs';
import { join } from 'path';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR!;

function readJSON(path: string) {
  try { return JSON.parse(readFileSync(path, 'utf-8')); } catch { return null; }
}

router.get('/', (_req: Request, res: Response) => {
  const today = new Date().toISOString().split('T')[0];

  // Messages today from activity.jsonl
  const activityFile = join(ORCHESTRA, 'activity.jsonl');
  let allEvents: any[] = [];
  if (existsSync(activityFile)) {
    allEvents = readFileSync(activityFile, 'utf-8').trim().split('\n')
      .filter(Boolean).map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
  }

  const todayEvents = allEvents.filter(e => e.timestamp?.startsWith(today));
  const messageEvents = todayEvents.filter(e => e.event === 'message_delivered');

  // Per-agent message counts
  const agentMessages: Record<string, { sent: number; received: number }> = {};
  for (const e of messageEvents) {
    const agent = e.agent || 'unknown';
    if (!agentMessages[agent]) agentMessages[agent] = { sent: 0, received: 0 };
    agentMessages[agent].received++;
  }

  // Task throughput (last 30 days)
  const tasksDir = join(ORCHESTRA, 'tasks');
  const allTasks: any[] = [];
  if (existsSync(tasksDir)) {
    for (const f of readdirSync(tasksDir).filter(f => f.endsWith('.json'))) {
      const task = readJSON(join(tasksDir, f));
      if (task) allTasks.push(task);
    }
  }

  const thirtyDaysAgo = new Date(Date.now() - 30 * 86400000).toISOString().split('T')[0];
  const completedTasks = allTasks.filter(t => ['complete', 'completed', 'done', 'reported'].includes(t.status));
  const throughputByDay: Record<string, number> = {};
  for (const t of completedTasks) {
    const day = (t.updated || t.updated_at || t.created || t.created_at || '').split('T')[0];
    if (day >= thirtyDaysAgo) {
      throughputByDay[day] = (throughputByDay[day] || 0) + 1;
    }
  }
  const throughput = Object.entries(throughputByDay)
    .map(([date, count]) => ({ date, count }))
    .sort((a, b) => a.date.localeCompare(b.date));

  // Agent effectiveness
  const registry = readJSON(join(ORCHESTRA, 'registry.json')) || { agents: {} };
  const agentEffectiveness = Object.entries(registry.agents || {}).map(([id, config]: [string, any]) => {
    const agentTasks = allTasks.filter(t => (t.agents_spawned || []).includes(id));
    const completed = agentTasks.filter(t => (t.agents_complete || []).includes(id)).length;
    const failed = agentTasks.filter(t => t.status === 'failed' && (t.agents_spawned || []).includes(id)).length;
    const total = completed + failed;

    // Sparkline data from activity (last 7 days)
    const sevenDaysAgo = new Date(Date.now() - 7 * 86400000).toISOString();
    const recentEvents = allEvents.filter(e => e.agent === id && e.timestamp >= sevenDaysAgo);
    const sparkline = Array.from({ length: 7 }, (_, i) => {
      const day = new Date(Date.now() - (6 - i) * 86400000).toISOString().split('T')[0];
      return recentEvents.filter(e => e.timestamp?.startsWith(day)).length;
    });

    return {
      id, name: config.name || id, tier: config.tier,
      tasks_done: completed, success_rate: total > 0 ? Math.round(completed / total * 100) : 100,
      errors: failed, sparkline
    };
  }).filter(a => a.tasks_done > 0 || a.errors > 0)
    .sort((a, b) => b.tasks_done - a.tasks_done);

  // Plan usage (from state/usage-scrape.json if exists)
  const usageFile = join(ORCHESTRA, 'state', 'usage-scrape.json');
  const usage = existsSync(usageFile) ? readJSON(usageFile) : null;

  res.json({
    messages_today: {
      delivered: messageEvents.length,
      pending: todayEvents.filter(e => e.event === 'context_requested').length,
      by_agent: agentMessages
    },
    task_throughput: throughput,
    agent_effectiveness: agentEffectiveness,
    plan_usage: usage,
    total_events_today: todayEvents.length,
    total_tasks: allTasks.length
  });
});

export default router;
