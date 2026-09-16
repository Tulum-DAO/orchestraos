import { Router } from 'express';
import { readFileSync, writeFileSync, existsSync, readdirSync, mkdirSync } from 'fs';
import { join } from 'path';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR!;
const EXPERIMENTS_DIR = join(ORCHESTRA, 'experiments');
if (!existsSync(EXPERIMENTS_DIR)) mkdirSync(EXPERIMENTS_DIR, { recursive: true });

function readJSON(path: string) {
  try { return JSON.parse(readFileSync(path, 'utf-8')); } catch { return null; }
}

router.get('/', (_, res) => {
  const files = existsSync(EXPERIMENTS_DIR) ? readdirSync(EXPERIMENTS_DIR).filter(f => f.endsWith('.json')) : [];
  const experiments = files.map(f => readJSON(join(EXPERIMENTS_DIR, f))).filter(Boolean)
    .sort((a: any, b: any) => new Date(b.created || 0).getTime() - new Date(a.created || 0).getTime());

  const active = experiments.filter((e: any) => ['proposed', 'running'].includes(e.status));
  const running = experiments.filter((e: any) => e.status === 'running');
  const completed = experiments.filter((e: any) => e.status === 'completed');
  const kept = completed.filter((e: any) => e.kept);

  res.json({
    experiments, total: experiments.length,
    active_cycles: active.length, running: running.length,
    completed: completed.length,
    keep_rate: completed.length > 0 ? Math.round(kept.length / completed.length * 100) : 0
  });
});

router.post('/', (req, res) => {
  const id = `exp_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
  const experiment = {
    id, status: 'proposed', created: new Date().toISOString(),
    ...req.body
  };
  writeFileSync(join(EXPERIMENTS_DIR, `${id}.json`), JSON.stringify(experiment, null, 2));
  res.json(experiment);
});

router.patch('/:id', (req, res) => {
  const file = join(EXPERIMENTS_DIR, `${req.params.id}.json`);
  if (!existsSync(file)) return res.status(404).json({ error: 'Not found' });
  const exp = JSON.parse(readFileSync(file, 'utf-8'));
  Object.assign(exp, req.body);
  if (req.body.status === 'running' && !exp.started_at) exp.started_at = new Date().toISOString();
  if (req.body.status === 'completed' && !exp.completed_at) exp.completed_at = new Date().toISOString();
  writeFileSync(file, JSON.stringify(exp, null, 2));
  res.json(exp);
});

export default router;
