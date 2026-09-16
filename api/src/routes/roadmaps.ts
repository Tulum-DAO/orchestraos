import { Router, type Request, type Response } from 'express';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { join } from 'path';
import { getRoadmaps } from '../services/state-reader.js';
import { loadConfig } from '../lib/config.js';

const router = Router();

interface Phase {
  id?: string;
  name?: string;
  status?: string;
  progress?: number;
  tasks?: Task[];
}

interface Task {
  status?: string;
  [key: string]: unknown;
}

interface ProjectRoadmap {
  name?: string;
  priority?: string;
  phases?: Phase[];
  [key: string]: unknown;
}

router.get('/', (_req: Request, res: Response) => {
  try {
    const raw = getRoadmaps();
    if (!raw) {
      res.status(404).json({ error: 'Roadmaps file not found' });
      return;
    }

    const projects = (raw.projects ?? raw) as Record<string, ProjectRoadmap>;
    const summaries = Object.entries(projects).map(([key, proj]) => {
      const phases = proj.phases || [];
      let totalTasks = 0;
      let completedTasks = 0;

      for (const phase of phases) {
        const tasks = phase.tasks || [];
        totalTasks += tasks.length;
        completedTasks += tasks.filter(
          (t) => t.status === 'complete' || t.status === 'completed' || t.status === 'done'
        ).length;
      }

      const progress = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;

      return {
        key,
        name: proj.name || key,
        priority: proj.priority || 'P2',
        phase_count: phases.length,
        total_tasks: totalTasks,
        completed_tasks: completedTasks,
        progress_pct: progress,
        phases,
      };
    });

    // Sort by priority
    summaries.sort((a, b) => a.priority.localeCompare(b.priority));

    res.json({ projects: summaries, total: summaries.length });
  } catch (err) {
    res.status(500).json({ error: 'Failed to load roadmaps', detail: String(err) });
  }
});

router.patch('/:project/tasks/:phaseIndex/:taskIndex', (req: Request, res: Response) => {
  const project = req.params.project as string;
  const phaseIndex = req.params.phaseIndex as string;
  const taskIndex = req.params.taskIndex as string;
  const { deployment_state } = req.body;

  const ROADMAPS_FILE = join(process.env.DASHBOARD_V4_DIR || join(loadConfig().dataDir, 'dashboard_v4'), 'roadmaps.json');
  if (!existsSync(ROADMAPS_FILE)) { res.status(404).json({ error: 'No roadmaps file' }); return; }

  const data = JSON.parse(readFileSync(ROADMAPS_FILE, 'utf-8'));
  const proj = data.projects?.[project as string];
  if (!proj) { res.status(404).json({ error: 'Project not found' }); return; }

  const phase = proj.phases?.[parseInt(phaseIndex as string)];
  if (!phase) { res.status(404).json({ error: 'Phase not found' }); return; }

  const task = phase.tasks?.[parseInt(taskIndex as string)];
  if (!task) { res.status(404).json({ error: 'Task not found' }); return; }

  task.deployment_state = deployment_state;
  writeFileSync(ROADMAPS_FILE, JSON.stringify(data, null, 2));
  res.json(task);
});

export default router;
