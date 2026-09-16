import { Router, type Request, type Response } from 'express';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { join } from 'path';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR!;
const CRONS_FILE = join(ORCHESTRA, 'state', 'crons.json');

function loadCrons() {
  if (!existsSync(CRONS_FILE)) return { crons: [] };
  try { return JSON.parse(readFileSync(CRONS_FILE, 'utf-8')); } catch { return { crons: [] }; }
}

function saveCrons(data: any) {
  writeFileSync(CRONS_FILE, JSON.stringify(data, null, 2));
}

router.get('/', (_req: Request, res: Response) => {
  try {
    const data = loadCrons();
    const crons = data.crons || [];

    // Group by agent
    const by_agent: Record<string, any[]> = {};
    for (const c of crons) {
      const agent = c.agent || 'system';
      if (!by_agent[agent]) by_agent[agent] = [];
      by_agent[agent].push(c);
    }

    const agents = Object.keys(by_agent).sort();

    res.json({
      crons,
      by_agent,
      agents,
      total: crons.length,
      enabled: crons.filter((c: any) => c.enabled).length,
    });
  } catch (err) {
    res.status(500).json({ error: 'Failed to load workflows', detail: String(err) });
  }
});

router.post('/', (req: Request, res: Response) => {
  try {
    const data = loadCrons();
    const newCron = {
      id: `cron_${Date.now()}`,
      ...req.body,
      enabled: true,
    };
    data.crons.push(newCron);
    saveCrons(data);
    res.json(newCron);
  } catch (err) {
    res.status(500).json({ error: 'Failed to create workflow', detail: String(err) });
  }
});

router.patch('/:id', (req: Request, res: Response) => {
  try {
    const data = loadCrons();
    const cron = data.crons.find((c: any) => c.id === req.params.id);
    if (!cron) {
      res.status(404).json({ error: 'Not found' });
      return;
    }
    Object.assign(cron, req.body);
    saveCrons(data);
    res.json(cron);
  } catch (err) {
    res.status(500).json({ error: 'Failed to update workflow', detail: String(err) });
  }
});

router.delete('/:id', (req: Request, res: Response) => {
  try {
    const data = loadCrons();
    data.crons = data.crons.filter((c: any) => c.id !== req.params.id);
    saveCrons(data);
    res.json({ deleted: req.params.id });
  } catch (err) {
    res.status(500).json({ error: 'Failed to delete workflow', detail: String(err) });
  }
});

export default router;
