/**
 * Arturo text bridge (tracks T2/T4) — the browser's path to Arturo's brain.
 *
 * POST /api/arturo/text {text, conversation_id?}  -> gateway POST /arturo/text -> :5071/text
 * GET  /api/arturo/health                          -> gateway GET  /arturo/health
 *
 * Same trust model as routes/voice.ts: this Node process is the only holder of the
 * gateway bearer; the browser never sees it and never reaches the gateway or :5071.
 * The token file and gateway URL come from `orchestra up`'s env (WATCH_GATEWAY_TOKEN_FILE /
 * WATCH_GATEWAY_URL); the legacy ~/.config/jarvis path is the fallback for a bare `node`.
 */
import { Router } from 'express';
import { readFileSync } from 'fs';

const router = Router();
const GATEWAY_TOKEN_FILE = process.env.WATCH_GATEWAY_TOKEN_FILE
  || `${process.env.HOME}/.config/jarvis/watch-gateway-token`;
const GATEWAY_URL = process.env.WATCH_GATEWAY_URL || 'http://127.0.0.1:8890';

export function gatewayToken(): string {
  try { return readFileSync(GATEWAY_TOKEN_FILE, 'utf-8').trim(); } catch { return ''; }
}

async function forward(res: any, path: string, init: RequestInit, timeoutMs: number) {
  const token = gatewayToken();
  if (!token) { res.status(502).json({ ok: false, error: 'gateway token unavailable' }); return; }
  try {
    const r = await fetch(`${GATEWAY_URL}${path}`, {
      ...init,
      headers: { ...(init.headers || {}), 'Authorization': `Bearer ${token}` },
      signal: AbortSignal.timeout(timeoutMs),
    });
    const body = await r.json().catch(() => ({ ok: false, error: 'bad gateway json' }));
    res.status(r.status).json(body);
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

export default router;
