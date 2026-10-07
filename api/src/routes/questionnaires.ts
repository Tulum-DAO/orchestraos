/**
 * questionnaires.ts — API routes for questionnaire management.
 * Lists, serves, and tracks questionnaires in the dashboard.
 */

import { fileURLToPath } from 'url';
import { Router, type Request, type Response } from 'express';
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { execFileSync } from 'child_process';
import { join, dirname } from 'path';
import { loadConfig } from '../lib/config.js';
import { submitQuestionnaire } from './questionnaireSubmit.js';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR || loadConfig().dataDir;
const CODE_ROOT = process.env.ORCHESTRA_ROOT || join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

function readJson(path: string): any {
  try {
    return JSON.parse(readFileSync(path, 'utf-8'));
  } catch {
    return null;
  }
}

// GET /api/questionnaires — list all questionnaires
router.get('/', (_req: Request, res: Response) => {
  const indexFile = join(ORCHESTRA, 'state', 'questionnaires', 'index.json');
  const questionnaires = readJson(indexFile) || [];

  // Count pending
  const pending = questionnaires.filter((q: any) => q.status === 'pending').length;

  res.json({ questionnaires, total: questionnaires.length, pending });
});

// GET /api/questionnaires/:id — get single questionnaire metadata
router.get('/:id', (req: Request, res: Response) => {
  const indexFile = join(ORCHESTRA, 'state', 'questionnaires', 'index.json');
  const questionnaires = readJson(indexFile) || [];
  const q = questionnaires.find((q: any) => q.id === req.params.id);

  if (!q) { res.status(404).json({ error: 'Questionnaire not found' }); return; }

  // Include response data if completed
  if (q.response_file) {
    const responsePath = join(ORCHESTRA, q.response_file);
    if (existsSync(responsePath)) {
      q.response = readJson(responsePath);
    }
  }

  res.json(q);
});

// GET /api/questionnaires/:id/html — serve the questionnaire HTML
router.get('/:id/html', (req: Request, res: Response) => {
  const indexFile = join(ORCHESTRA, 'state', 'questionnaires', 'index.json');
  const questionnaires = readJson(indexFile) || [];
  const q = questionnaires.find((q: any) => q.id === req.params.id);

  if (!q || !q.html_file) { res.status(404).json({ error: 'Not found' }); return; }

  const htmlPath = join(ORCHESTRA, q.html_file);
  if (!existsSync(htmlPath)) { res.status(404).json({ error: 'HTML file missing' }); return; }

  let html = readFileSync(htmlPath, 'utf-8');

  // If completed, inject saved answers back into the form
  if (q.response_file) {
    const responsePath = join(ORCHESTRA, q.response_file);
    if (existsSync(responsePath)) {
      const response = readJson(responsePath);
      if (response?.answers) {
        const inject = `<script>
(function() {
  const saved = ${JSON.stringify(response.answers)};
  Object.entries(saved).forEach(function(entry) {
    const qid = entry[0];
    const data = entry[1];
    if (data.value) {
      const radio = document.querySelector('input[name="' + qid + '"][value="' + data.value + '"]');
      if (radio) { radio.checked = true; radio.dispatchEvent(new Event('change', {bubbles:true})); }
    }
    if (data.notes) {
      const ta = document.querySelector('textarea[data-other="' + qid + '"]');
      if (ta) ta.value = data.notes;
    }
  });
  if (typeof updateProgress === 'function') updateProgress();
})();
</script>`;
        html = html.replace('</body>', inject + '</body>');
      }
    }
  }

  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.send(html);
});

// GET /api/questionnaires/:id/response — get submitted answers
router.get('/:id/response', (req: Request, res: Response) => {
  const indexFile = join(ORCHESTRA, 'state', 'questionnaires', 'index.json');
  const questionnaires = readJson(indexFile) || [];
  const q = questionnaires.find((q: any) => q.id === req.params.id);

  if (!q) { res.status(404).json({ error: 'Not found' }); return; }
  if (!q.response_file) { res.json({ submitted: false }); return; }

  const responsePath = join(ORCHESTRA, q.response_file);
  if (!existsSync(responsePath)) { res.json({ submitted: false }); return; }

  res.json({ submitted: true, ...readJson(responsePath) });
});

// POST /api/questionnaires/:id/submit — Server-side submission
// Handles full lifecycle: save feedback, update index, notify creating agent
// Code lives in the CHECKOUT, not the data dir (5c3636c): under Docker / a separate data dir /
// `orchestra init --demo`, <ORCHESTRA_DIR>/msg_store.py does not exist and the creator would never
// be notified, silently (review of #195). Exported so a test pins it.
export const MSG_STORE = join(CODE_ROOT, 'msg_store.py');

router.post('/:id/submit', (req: Request, res: Response) => {
  // All of the logic, and every guard, lives in submitQuestionnaire (tested end to end there).
  const r = submitQuestionnaire({
    orchestraDir: ORCHESTRA,
    sender: loadConfig().operatorId,
    readJson,
    writeFile: (path, data) => writeFileSync(path, data),
    ensureDir: (dir) => { if (!existsSync(dir)) mkdirSync(dir, { recursive: true }); },
    send: (argv) => { execFileSync('python3', [MSG_STORE, ...argv],
      { encoding: 'utf-8', timeout: 10000, cwd: ORCHESTRA }); },
    now: () => Date.now(),
  }, String(req.params.id), req.body);
  res.status(r.status).json(r.body);
});

export default router;
