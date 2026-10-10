import { Router, type Request, type Response } from 'express';
import { execFileSync } from 'child_process';
import { loadConfig } from '../lib/config.js';
import { capabilityPath, hasCapability, notAvailable } from '../lib/capabilities.js';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR || loadConfig().dataDir;

router.get('/', (_req: Request, res: Response) => {
  if (!hasCapability('projectStatus')) { notAvailable(res, 'projectStatus'); return; }
  try {
    const script = capabilityPath('projectStatus');   // code, not data (lib/capabilities.ts)
    const out = execFileSync('python3', [script], {
      timeout: 15000,
      encoding: 'utf-8',
      cwd: ORCHESTRA,
    });
    res.json(JSON.parse(out.trim()));
  } catch (err: any) {
    res.status(500).json({ error: 'project-status failed', detail: err.message });
  }
});

export default router;
