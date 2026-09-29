/**
 * Arturo text bridge (tracks T2/T4) — the browser's path to Arturo's brain and its threads.
 *
 * POST /api/arturo/text        {text, conversation_id?, brain?, context?} -> gateway /arturo/text -> :5071/text
 * GET  /api/arturo/health                               -> gateway /arturo/health
 * GET  /api/arturo/threads     [?limit=&offset=]        -> gateway /arturo/threads      (G20)
 * GET  /api/arturo/threads/:id                          -> gateway /arturo/threads/{id} (G20)
 * POST /api/arturo/transcribe  multipart {audio}       -> gateway /arturo/transcribe -> :5071/transcribe (item C)
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
  /** Item C: stream a request body (multipart dictation clip) upstream untouched — fetchJson parses
   *  JSON and takes a materialised init, so the relay has its own seam. Returns status + parsed body. */
  forwardStream: (url: string, req: any, headers: Record<string, string>, timeoutMs: number) => Promise<{ status: number; body: any }>;
}

// Per-turn brain + page context (DEC-1790669162399904 §1.1-1.2). Shape checks only, for an early
// 400 with the proxy's own error names. The proxy is authoritative: it alone holds the model
// catalog, so a well-shaped but unlisted model is refused there. Only known fields are forwarded.
const MODEL_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,99}$/;
const CTX_FIELDS = ['route', 'entityKind', 'entityId', 'hint'] as const;
const CTX_MAX = 300;

type Refusal = { error: string; field: string };
type Picked<T> = { ok: true; value: T } | { ok: false; refusal: Refusal };

export function pickBrain(raw: unknown): Picked<{ provider: string; model: string }> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return { ok: false, refusal: { error: 'bad_brain', field: 'brain' } };
  const { provider, model = '' } = raw as Record<string, unknown>;
  if (typeof provider !== 'string' || !provider || provider.length > 40) return { ok: false, refusal: { error: 'bad_brain', field: 'brain.provider' } };
  if (typeof model !== 'string' || (model !== '' && !MODEL_ID_RE.test(model))) return { ok: false, refusal: { error: 'unknown_model', field: 'brain.model' } };
  return { ok: true, value: { provider, model } };
}

export function pickContext(raw: unknown): Picked<Record<string, string>> {
  const bad: Picked<Record<string, string>> = { ok: false, refusal: { error: 'bad_context', field: 'context' } };
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return bad;
  const src = raw as Record<string, unknown>;
  if (typeof src.route !== 'string' || !src.route) return bad;
  const out: Record<string, string> = {};
  for (const k of CTX_FIELDS) {
    const v = src[k];
    if (v === undefined || v === null) continue;
    if (typeof v !== 'string' || v.length > CTX_MAX) return bad;
    out[k] = v;
  }
  return { ok: true, value: out };
}

export function defaultArturoDeps(): ArturoDeps {
  return {
    token: gatewayToken,
    fetchJson: async (url, init, timeoutMs) => {
      const r = await fetch(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
      const body = await r.json().catch(() => ({ ok: false, error: 'bad gateway json' }));
      return { status: r.status, body };
    },
    forwardStream: async (url, req, headers, timeoutMs) => {
      // Node 22 fetch: a Readable body needs duplex:'half'. Content-Type carries the multipart boundary.
      const r = await fetch(url, {
        method: 'POST', headers, body: req, duplex: 'half', signal: AbortSignal.timeout(timeoutMs),
      } as RequestInit);
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
    const upstream: Record<string, unknown> = { text, conversation_id };
    for (const [key, pick] of [['brain', pickBrain], ['context', pickContext]] as const) {
      const raw = (req.body || {})[key];
      if (raw === undefined || raw === null) continue;
      const picked = pick(raw);
      if (!picked.ok) { res.status(400).json({ ok: false, ...picked.refusal }); return; }
      upstream[key] = picked.value;
    }
    await forward(res, '/arturo/text', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(upstream),
    }, 195000);
  });

  // Item C — web dictation tier 2: the browser records (MediaRecorder) and the box transcribes
  // (local faster-whisper, no vendor key). Multipart streams through untouched; express.json only
  // parses application/json so it never touches this body. 40 s > gateway 35 s > proxy 25 s.
  router.post('/transcribe', async (req, res) => {
    const token = deps.token();
    if (!token) { res.status(502).json({ ok: false, error: 'gateway token unavailable' }); return; }
    const ct = String(req.headers['content-type'] || '');
    if (!ct.startsWith('multipart/form-data')) { res.status(400).json({ ok: false, error: 'multipart body required' }); return; }
    const headers: Record<string, string> = { 'Authorization': `Bearer ${token}`, 'Content-Type': ct };
    if (req.headers['content-length']) headers['Content-Length'] = String(req.headers['content-length']);
    try {
      const { status, body } = await deps.forwardStream(`${GATEWAY_URL}/arturo/transcribe`, req, headers, 40000);
      res.status(status).json(body);
    } catch (err: any) {
      res.status(502).json({ ok: false, error: 'gateway unreachable', detail: err.message });
    }
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
