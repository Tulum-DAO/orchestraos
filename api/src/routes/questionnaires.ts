/**
 * questionnaires.ts — API routes for questionnaire management.
 * Lists, serves, and tracks questionnaires in the dashboard.
 *
 * Two stores. LIVE questionnaires (every one `approval.py questionnaire` makes) live in the
 * gateway's QuestionnaireStore (tasks.db) and are what the phone and watch answer; LEGACY ones
 * are files listed in state/questionnaires/index.json, each with an html_file the page shows in an
 * iframe. Live ones are FORWARDED to the gateway's own routes (GET /questionnaires,
 * GET /questionnaires/{id}, POST /questionnaires/{id}/submit) with the gateway bearer, the same
 * pattern as routes/away.ts: one validator and one resume path for every surface, and the browser
 * never sees the token. The web renders them natively from the detail's questions[]; /:id/html and
 * /:id/submit stay legacy-only.
 */

import { injectSavedAnswers } from './questionnaireInject.js';
import { fileURLToPath } from 'url';
import { Router, type Request, type Response } from 'express';
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { execFileSync } from 'child_process';
import { join, dirname } from 'path';
import { loadConfig } from '../lib/config.js';
import { readGatewayToken } from '../lib/gateway-token.js';
import { gatewayUrl } from '../lib/gateway-url.js';
import { principal as defaultPrincipal, type Principal } from '../lib/principal.js';
import { submitQuestionnaire } from './questionnaireSubmit.js';

const CODE_ROOT = process.env.ORCHESTRA_ROOT || join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

export interface QuestionnairesDeps {
  orchestraDir: string;
  token: () => string;
  gatewayUrl: () => string;
  fetchJson: (url: string, init: RequestInit, timeoutMs: number) => Promise<{ status: number; body: any }>;
  /** Who is calling (lib/principal): null = no identity. Answering needs an operator identity. */
  principal: (req: Request) => Principal | null;
}

export function defaultQuestionnairesDeps(): QuestionnairesDeps {
  return {
    orchestraDir: process.env.ORCHESTRA_DIR || loadConfig().dataDir,
    token: readGatewayToken,
    gatewayUrl: () => gatewayUrl(),
    fetchJson: async (url, init, timeoutMs) => {
      const r = await fetch(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
      const body = await r.json().catch(() => ({ ok: false, error: 'bad gateway json' }));
      return { status: r.status, body };
    },
    principal: defaultPrincipal,
  };
}

// Code lives in the CHECKOUT, not the data dir (5c3636c): under Docker / a separate data dir /
// `orchestra init --demo`, <ORCHESTRA_DIR>/msg_store.py does not exist and the creator would never
// be notified, silently (review of #195). Exported so a test pins it.
export const MSG_STORE = join(CODE_ROOT, 'msg_store.py');

/** A live (QuestionnaireStore) id. Legacy ids never start with it. */
export function isLiveId(id: string): boolean {
  return /^qnr_[A-Za-z0-9_]+$/.test(id);
}

function readJson(path: string): any {
  try {
    return JSON.parse(readFileSync(path, 'utf-8'));
  } catch {
    return null;
  }
}

export function createQuestionnairesRouter(deps: QuestionnairesDeps = defaultQuestionnairesDeps()): Router {
const router = Router();
const ORCHESTRA = deps.orchestraDir;

async function gateway(path: string, init: RequestInit): Promise<{ status: number; body: any }> {
  const token = deps.token();
  if (!token) return { status: 502, body: { ok: false, error: 'gateway token unavailable' } };
  try {
    return await deps.fetchJson(`${deps.gatewayUrl()}${path}`, {
      ...init, headers: { ...(init.headers || {}), Authorization: `Bearer ${token}` },
    }, 10000);
  } catch (err: any) {
    return { status: 502, body: { ok: false, error: 'gateway unreachable', detail: err?.message } };
  }
}

// GET /api/questionnaires — live (pending, from the gateway) first, then the legacy history.
// A live row wins on an id collision. With the gateway down the legacy list is still served.
router.get('/', async (_req: Request, res: Response) => {
  const indexFile = join(ORCHESTRA, 'state', 'questionnaires', 'index.json');
  const legacy = readJson(indexFile) || [];
  const g = await gateway('/questionnaires', { method: 'GET' });
  const live = (g.status === 200 && Array.isArray(g.body?.questionnaires) ? g.body.questionnaires : [])
    // the page's cards read created_by / description (the legacy shape); a live row has from_agent / summary
    .map((q: any) => ({ ...q, status: q.status || 'pending', source: 'live',
      created_by: q.created_by ?? q.from_agent, description: q.description ?? q.summary ?? '' }));
  const liveIds = new Set(live.map((q: any) => q.id));
  const questionnaires = [...live, ...legacy.filter((q: any) => !liveIds.has(q.id))];
  const pending = questionnaires.filter((q: any) => q.status === 'pending').length;
  res.json({ questionnaires, total: questionnaires.length, pending });
});

// GET /api/questionnaires/:id — a live id: the gateway's detail (questions[], menus, answers);
// otherwise the legacy metadata.
router.get('/:id', async (req: Request, res: Response) => {
  const id = String(req.params.id);
  if (isLiveId(id)) {
    const g = await gateway(`/questionnaires/${encodeURIComponent(id)}`, { method: 'GET' });
    if (g.status === 200 && g.body?.questionnaire) { res.json({ ...g.body.questionnaire, source: 'live' }); return; }
    if (g.status !== 404) { res.status(g.status).json(g.body); return; }
  }
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
  if (isLiveId(String(req.params.id))) { res.status(404).json({ error: 'Not found' }); return; }   // live ids: GET /:id + /answers only
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
        // P0c1: escaping AND insertion both live in questionnaireInject.ts — see the two holes there.
        html = injectSavedAnswers(html, response.answers);
      }
    }
  }

  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.send(html);
});

// GET /api/questionnaires/:id/response — get submitted answers
router.get('/:id/response', (req: Request, res: Response) => {
  if (isLiveId(String(req.params.id))) { res.status(404).json({ error: 'Not found' }); return; }   // live ids: GET /:id + /answers only
  const indexFile = join(ORCHESTRA, 'state', 'questionnaires', 'index.json');
  const questionnaires = readJson(indexFile) || [];
  const q = questionnaires.find((q: any) => q.id === req.params.id);

  if (!q) { res.status(404).json({ error: 'Not found' }); return; }
  if (!q.response_file) { res.json({ submitted: false }); return; }

  const responsePath = join(ORCHESTRA, q.response_file);
  if (!existsSync(responsePath)) { res.json({ submitted: false }); return; }

  res.json({ submitted: true, ...readJson(responsePath) });
});

// POST /api/questionnaires/:id/answers — answer a LIVE questionnaire: forwarded to the gateway's
// own submit (validation, store, resume of the asking agent), surface "web". The gateway's answer
// (applied, missing, or a 400 naming the bad answer) is passed back as is.
router.post('/:id/answers', async (req: Request, res: Response) => {
  // Identity first (lib/principal). Behind an identity proxy (ORCHESTRA_API_TRUST_IDENTITY_HEADERS)
  // no identity is a 401 and a client-scoped viewer cannot answer the operator's questionnaires;
  // nothing reaches the gateway in either case. Without a proxy every caller is the configured
  // operator, as on every other route of this API (see lib/principal.ts).
  const who = deps.principal(req);
  if (!who) { res.status(401).json({ ok: false, error: 'unauthenticated' }); return; }
  if (who.clientScope) { res.status(403).json({ ok: false, error: 'a client-scoped user cannot answer questionnaires' }); return; }
  const id = String(req.params.id);
  if (!isLiveId(id)) { res.status(404).json({ ok: false, error: 'not a live questionnaire' }); return; }
  const answers = req.body?.answers;
  if (!answers || typeof answers !== 'object' || Array.isArray(answers)) {
    res.status(400).json({ ok: false, error: 'answers must be an object keyed by question number' }); return;
  }
  const g = await gateway(`/questionnaires/${encodeURIComponent(id)}/submit`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answers, surface: 'web' }),
  });
  res.status(g.status).json(g.body);
});

// POST /api/questionnaires/:id/submit — Server-side submission
// Handles full lifecycle: save feedback, update index, notify creating agent

router.post('/:id/submit', (req: Request, res: Response) => {
  if (isLiveId(String(req.params.id))) { res.status(404).json({ error: 'Not found' }); return; }   // live ids: GET /:id + /answers only
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

return router;
}

export default createQuestionnairesRouter();
