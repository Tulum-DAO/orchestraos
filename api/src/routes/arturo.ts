/**
 * Arturo text bridge (tracks T2/T4) — the browser's path to Arturo's brain and its threads.
 *
 * POST /api/arturo/text        {text, conversation_id?} -> gateway /arturo/text -> :5071/text
 * GET  /api/arturo/health                               -> gateway /arturo/health
 * GET  /api/arturo/threads     [?limit=&offset=]        -> gateway /arturo/threads      (G20)
 * GET  /api/arturo/threads/:id                          -> gateway /arturo/threads/{id} (G20)
 *
 * Same trust model as routes/voice.ts: this Node process is the only holder of the
 * gateway bearer; the browser never sees it and never reaches the gateway or :5071.
 * The token file and gateway URL come from `orchestra up`'s env (WATCH_GATEWAY_TOKEN_FILE /
 * WATCH_GATEWAY_URL); the legacy ~/.config/jarvis path is the fallback for a bare `node`.
 *
 * G20: the thread list is served from the SERVICE, not from the browser's localStorage, so
 * the home, the pill and the phone all read one thread space — and a thread outlives both a
 * browser reload and a service restart.
 */
import { Router } from 'express';
import { readFileSync } from 'fs';

const GATEWAY_TOKEN_FILE = process.env.WATCH_GATEWAY_TOKEN_FILE
  || `${process.env.HOME}/.config/jarvis/watch-gateway-token`;
export const GATEWAY_URL = process.env.WATCH_GATEWAY_URL || 'http://127.0.0.1:8890';

export function gatewayToken(): string {
  try { return readFileSync(GATEWAY_TOKEN_FILE, 'utf-8').trim(); } catch { return ''; }
}

export interface ArturoDeps {
  /** The gateway bearer. '' means "unavailable" — we never call the gateway without it. */
  token: () => string;
  /** One authed round trip; returns the upstream status so it can be passed through. */
  fetchJson: (url: string, init: RequestInit, timeoutMs: number) => Promise<{ status: number; body: any }>;
}

export function defaultArturoDeps(): ArturoDeps {
  return {
    token: gatewayToken,
    fetchJson: async (url, init, timeoutMs) => {
      const r = await fetch(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
      const body = await r.json().catch(() => ({ ok: false, error: 'bad gateway json' }));
      return { status: r.status, body };
    },
  };
}

export function createArturoRouter(deps: ArturoDeps = defaultArturoDeps()): Router {
  const router = Router();

  async function forward(res: any, path: string, init: RequestInit, timeoutMs: number) {
    const token = deps.token();
    if (!token) { res.status(502).json({ ok: false, error: 'gateway token unavailable' }); return; }
    try {
      const { status, body } = await deps.fetchJson(`${GATEWAY_URL}${path}`, {
        ...init,
        headers: { ...(init.headers || {}), 'Authorization': `Bearer ${token}` },
      }, timeoutMs);
      res.status(status).json(body);
    } catch (err: any) {
      res.status(502).json({ ok: false, error: 'gateway unreachable', detail: err.message });
    }
  }

  router.post('/text', async (req, res) => {
    const text = String((req.body || {}).text || '').trim();
    if (!text) { res.status(400).json({ ok: false, error: 'text required' }); return; }
    const conversation_id = String((req.body || {}).conversation_id || '').slice(0, 200);
    await forward(res, '/arturo/text', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, conversation_id }),
    }, 195000);
  });

  router.get('/health', async (_req, res) => {
    await forward(res, '/arturo/health', { method: 'GET' }, 6000);
  });

  // G20 — the thread list. Summaries only: the switcher renders without loading a transcript.
  router.get('/threads', async (req, res) => {
    const q = new URLSearchParams();
    for (const key of ['limit', 'offset']) {
      const v = req.query[key];
      if (v !== undefined) q.set(key, String(v));
    }
    const suffix = q.toString() ? `?${q}` : '';
    await forward(res, `/arturo/threads${suffix}`, { method: 'GET' }, 10000);
  });

  // G20 — one thread with its turns. The id is operator data, so it is escaped, not pasted.
  router.get('/threads/:id', async (req, res) => {
    const id = encodeURIComponent(String(req.params.id || '').slice(0, 200));
    if (!id) { res.status(400).json({ ok: false, error: 'thread id required' }); return; }
    await forward(res, `/arturo/threads/${id}`, { method: 'GET' }, 10000);
  });

  return router;
}

export default createArturoRouter();
