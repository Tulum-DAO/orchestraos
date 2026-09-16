/**
 * questionnaires.ts — API routes for questionnaire management.
 * Lists, serves, and tracks questionnaires in the dashboard.
 */

import { Router, type Request, type Response } from 'express';
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { execFileSync } from 'child_process';
import { join } from 'path';
import { loadConfig } from '../lib/config.js';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR || loadConfig().dataDir;

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
router.post('/:id/submit', (req: Request, res: Response) => {
  const qId = String(req.params.id);
  const { answers, responses, submitted_by } = req.body;
  const payload = answers || responses;

  if (!payload) {
    res.status(400).json({ error: 'answers or responses required' });
    return;
  }

  const indexFile = join(ORCHESTRA, 'state', 'questionnaires', 'index.json');
  const questionnaires = readJson(indexFile) || [];
  const qIndex = questionnaires.findIndex((q: any) => q.id === qId);

  const ts = Date.now();
  const feedbackDir = join(ORCHESTRA, 'state', 'feedback');
  if (!existsSync(feedbackDir)) mkdirSync(feedbackDir, { recursive: true });
  const feedbackFile = `state/feedback/${qId}_${ts}.json`;
  const feedbackData = {
    questionnaire_id: qId,
    answers: payload,
    submitted_by: submitted_by || loadConfig().operatorId,
    submitted_at: new Date().toISOString(),
  };
  writeFileSync(join(ORCHESTRA, feedbackFile), JSON.stringify(feedbackData, null, 2));

  if (qIndex >= 0) {
    questionnaires[qIndex].status = 'completed';
    questionnaires[qIndex].response_file = feedbackFile;
    questionnaires[qIndex].completed_at = new Date().toISOString();
    writeFileSync(indexFile, JSON.stringify(questionnaires, null, 2));
  }

  const createdBy = qIndex >= 0 ? questionnaires[qIndex].created_by : null;
  const title = qIndex >= 0 ? questionnaires[qIndex].title : qId;
  if (createdBy) {
    try {
      const msgStore = join(ORCHESTRA, 'msg_store.py');
      execFileSync('python3', [
        msgStore, 'send',
        '--from', submitted_by || loadConfig().operatorId,
        '--to', createdBy,
        '--type', 'questionnaire_completed',
        '--subject', `Questionnaire completed: ${title}`,
        '--body', JSON.stringify(feedbackData),
      ], { encoding: 'utf-8', timeout: 10000, cwd: ORCHESTRA });
    } catch (err: any) {
      console.error(`[questionnaires] Failed to notify ${createdBy}:`, err.message);
    }
  }

  res.json({
    submitted: true,
    questionnaire_id: qId,
    feedback_file: feedbackFile,
    notified_agent: createdBy || null,
  });
});

export default router;
