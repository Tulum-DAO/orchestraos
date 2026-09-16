import { Router, type Request, type Response } from 'express';
import { readFileSync, writeFileSync, existsSync, readdirSync, appendFileSync } from 'fs';
import { join } from 'path';
import { getMacStatus, getUnifiedAgentStatus } from '../services/cross-machine.js';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR!;
const TRANSIT_FILE = join(ORCHESTRA, 'state', 'transit.json');

function loadTransit() {
  if (!existsSync(TRANSIT_FILE)) return { active: false };
  try { return JSON.parse(readFileSync(TRANSIT_FILE, 'utf-8')); } catch { return { active: false }; }
}

function saveTransit(data: any) {
  writeFileSync(TRANSIT_FILE, JSON.stringify(data, null, 2));
}

// GET /api/transit/status
router.get('/status', (_req: Request, res: Response) => {
  const transit = loadTransit();
  const macState = getMacStatus();
  res.json({ ...transit, mac_status: macState.status });
});

// POST /api/transit/go-dark — trigger transit mode
router.post('/go-dark', async (_req: Request, res: Response) => {
  try {
    const registryPath = join(ORCHESTRA, 'registry.json');
    const registry = JSON.parse(readFileSync(registryPath, 'utf-8'));

    // Snapshot Mac agents
    const unified = await getUnifiedAgentStatus(registry);
    const macAgents = Object.values(unified).filter((a: any) => a.machine === 'mac');
    const aliveOnMac = macAgents.filter((a: any) => a.tmux_alive);

    // Read active tasks for Mac agents
    const tasksDir = join(ORCHESTRA, 'tasks');
    const activeTasks: any[] = [];
    if (existsSync(tasksDir)) {
      for (const f of readdirSync(tasksDir).filter((f: string) => f.endsWith('.json'))) {
        try {
          const task = JSON.parse(readFileSync(join(tasksDir, f), 'utf-8'));
          if (['in_progress', 'partial', 'routing'].includes(task.status)) {
            const hasMacAgent = (task.agents_spawned || []).some((a: string) =>
              macAgents.some((ma: any) => ma.agent_id === a)
            );
            if (hasMacAgent) activeTasks.push(task);
          }
        } catch {}
      }
    }

    const transit = {
      active: true,
      started_at: new Date().toISOString(),
      ended_at: null,
      trigger: 'manual',
      mac_agents_snapshot: aliveOnMac.map((a: any) => a.agent_id),
      tasks_continued: activeTasks.map((t: any) => ({
        task_id: t.task_id,
        description: t.description,
        priority: t.priority,
        agents: t.agents_spawned
      })),
      shadow_pms_spawned: [],
      tasks_completed_during_transit: [],
      report: null
    };

    // Log activity
    const activityFile = join(ORCHESTRA, 'activity.jsonl');
    const event = JSON.stringify({
      timestamp: new Date().toISOString(),
      agent: 'system',
      event: 'transit_started',
      detail: `Going dark. ${aliveOnMac.length} Mac agents snapshotted, ${activeTasks.length} tasks to continue.`
    });
    try { appendFileSync(activityFile, event + '\n'); } catch {}

    saveTransit(transit);
    res.json({ status: 'transit_active', snapshot: transit });
  } catch (err) {
    res.status(500).json({ error: 'Transit failed', detail: String(err) });
  }
});

// POST /api/transit/return — end transit mode
router.post('/return', (_req: Request, res: Response) => {
  try {
    const transit = loadTransit();
    if (!transit.active) {
      res.json({ status: 'not_in_transit' });
      return;
    }

    const duration = transit.started_at
      ? Math.round((Date.now() - new Date(transit.started_at).getTime()) / 60000)
      : 0;

    transit.active = false;
    transit.ended_at = new Date().toISOString();
    transit.report = {
      duration_minutes: duration,
      mac_agents_recovered: transit.mac_agents_snapshot?.length || 0,
      tasks_continued: transit.tasks_continued?.length || 0,
      tasks_completed: transit.tasks_completed_during_transit?.length || 0,
      summary: `Transit lasted ${duration} minutes. ${transit.tasks_completed_during_transit?.length || 0} tasks completed while away.`
    };

    // Log activity
    const activityFile = join(ORCHESTRA, 'activity.jsonl');
    const event = JSON.stringify({
      timestamp: new Date().toISOString(),
      agent: 'system',
      event: 'transit_ended',
      detail: `Back online. Transit lasted ${duration}m. ${transit.tasks_completed_during_transit?.length || 0} tasks completed.`
    });
    try { appendFileSync(activityFile, event + '\n'); } catch {}

    saveTransit(transit);
    res.json({ status: 'transit_ended', report: transit.report });
  } catch (err) {
    res.status(500).json({ error: 'Return failed', detail: String(err) });
  }
});

// GET /api/transit/report
router.get('/report', (_req: Request, res: Response) => {
  const transit = loadTransit();
  res.json(transit.report || null);
});

export default router;
