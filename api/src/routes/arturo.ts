/**
 * Arturo text bridge (tracks T2/T4) — the browser's path to Arturo's brain and its threads.
 *
 * POST /api/arturo/text        {text, conversation_id?, brain?, context?} -> gateway /arturo/text -> :5071/text
 * GET  /api/arturo/health                               -> gateway /arturo/health
 * GET  /api/arturo/threads     [?limit=&offset=]        -> gateway /arturo/threads      (G20)
 * GET  /api/arturo/threads/:id                          -> gateway /arturo/threads/{id} (G20)
 * POST /api/arturo/transcribe  multipart {audio}       -> gateway /arturo/transcribe -> :5071/transcribe (item C)
 * GET  /api/arturo/ptt/vendor                           -> gateway /arturo/ptt/vendor  (which voice providers are set up)
 * GET  /api/arturo/ptt/voices  ?vendor=                 -> gateway /arturo/ptt/voices  (the server's voice rows)
 * GET  /api/arturo/ptt/voice   ?vendor=                 -> gateway /arturo/ptt/voice   (the voice the next call uses)
 * PUT  /api/arturo/ptt/voice   {vendor, voice_id}       -> gateway /arturo/ptt/voice   (admins only; the server validates it)
 * GET  /api/arturo/avatars                              -> gateway /arturo/avatars     (every saved avatar look)
 * GET  /api/arturo/avatar      ?agent=                  -> gateway /arturo/avatar      (one agent's look, or its default)
 * PUT  /api/arturo/avatar      {agent, shape?, color?, eyeColor?} -> gateway /arturo/avatar (admins only)
 * DELETE /api/arturo/avatar    ?agent=                  -> gateway /arturo/avatar      (admins only: back to the default)
 * GET  /api/arturo/name                                 -> gateway /arturo/assistant/name (what the assistant is called)
 * PUT  /api/arturo/name        {name}                   -> gateway /arturo/assistant/name (admins only; the server validates it)
 * DELETE /api/arturo/name                               -> gateway /arturo/assistant/name (admins only: back to "Arturo")
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
import { gatewayUrl } from '../lib/gateway-url.js';
import { readFileSync } from 'fs';
import { loadConfig } from '../lib/config.js';
import { tenantScope } from '../lib/principal.js';

const GATEWAY_TOKEN_FILE = process.env.WATCH_GATEWAY_TOKEN_FILE
  || `${process.env.HOME}/.config/jarvis/watch-gateway-token`;
export const GATEWAY_URL = gatewayUrl();  // #84 class: env, then [gateway] in orchestra.toml

export function gatewayToken(): string {
  try { return readFileSync(GATEWAY_TOKEN_FILE, 'utf-8').trim(); } catch { return ''; }
}

export interface ArturoDeps {
  /** Whether this install has opted into Arturo at all. Kept injectable so the
   * disabled state is tested without reading the host's real configuration. */
  enabled?: () => boolean;
  /** The gateway bearer. '' means "unavailable" — we never call the gateway without it. */
  token: () => string;
  /** One authed round trip; returns the upstream status so it can be passed through. */
  fetchJson: (url: string, init: RequestInit, timeoutMs: number) => Promise<{ status: number; body: any }>;
  /** Item C: stream a request body (multipart dictation clip) upstream untouched — fetchJson parses
   *  JSON and takes a materialised init, so the relay has its own seam. Returns status + parsed body. */
  pipeStream?: (url: string, headers: Record<string, string>, body: string, timeoutMs: number) =>
    Promise<{ status: number; contentType: string; body: unknown; json: () => Promise<any> }>;
  forwardStream: (url: string, req: any, headers: Record<string, string>, timeoutMs: number) => Promise<{ status: number; body: any }>;
  /** Whether the caller is an admin (the same role check /api/usage uses). Injectable so both
   *  answers are tested without identity headers; absent means that real check. */
  isAdmin?: (req: any) => boolean;
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
    enabled: () => loadConfig().arturoEnabled,
    token: gatewayToken,
    fetchJson: async (url, init, timeoutMs) => {
      const r = await fetch(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
      const body = await r.json().catch(() => ({ ok: false, error: 'bad gateway json' }));
      return { status: r.status, body };
    },
    pipeStream: async (url, headers, body, timeoutMs) => {
      const r = await fetch(url, { method: 'POST', headers, body, signal: AbortSignal.timeout(timeoutMs) });
      return { status: r.status, contentType: r.headers.get('content-type') || '', body: r.body, json: async () => r.json().catch(() => ({ ok: false, error: 'bad gateway json' })) };
    },
    forwardStream: async (url, req, headers, timeoutMs) => {
      // Node 22 fetch: a Readable body needs duplex:'half'. Content-Type carries the multipart boundary.
      const r = await fetch(url, {
        method: 'POST', headers, body: req, duplex: 'half', signal: AbortSignal.timeout(timeoutMs),
      } as RequestInit);
      const body = await r.json().catch(() => ({ ok: false, error: 'bad gateway json' }));
      return { status: r.status, body };
    },
    isAdmin: (req) => tenantScope(req).isAdmin,
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
    // The client's id for this send (DEC-1791518421640932): the proxy validates it and replays a send it has seen.
    const turnId = (req.body || {}).turn_id;
    if (typeof turnId === 'string' && turnId) upstream.turn_id = turnId.slice(0, 64);
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

  // A turn that died in the operator's browser reports where, so it reaches a log on the box.
  // Only a short, flat record is kept: stage, error, timings, user agent — never message text.
  router.post('/client-log', (req, res) => {
    const b = (req.body && typeof req.body === 'object') ? req.body : {};
    const keep: Record<string, string> = {};
    for (const k of ['stage', 'error', 'stream_error', 'started', 'ms', 'ua', 'at']) {
      if (b[k] !== undefined) keep[k] = String(b[k]).slice(0, 200);
    }
    console.warn(`[arturo-client] ${JSON.stringify(keep)}`);
    res.status(204).end();
  });

  // Start the conversation's warm CLI before its first message, so turn one does not pay for
  // the process start. Fire-and-forget from the page; idempotent on the proxy.
  router.post('/text/prewarm', async (req, res) => {
    const conversation_id = String((req.body || {}).conversation_id || '').slice(0, 200);
    if (!conversation_id) { res.status(400).json({ ok: false, error: 'conversation_id required' }); return; }
    const upstream: Record<string, unknown> = { conversation_id };
    const raw = (req.body || {}).brain;
    if (raw !== undefined && raw !== null) {
      const picked = pickBrain(raw);
      if (!picked.ok) { res.status(400).json({ ok: false, ...picked.refusal }); return; }
      upstream.brain = picked.value;
    }
    await forward(res, '/arturo/text/prewarm', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(upstream),
    }, 30000);
  });

  /**
   * POST /api/arturo/text/stream — the same turn as /text, as it happens (spec §2.1).
   *
   * Piped, never buffered: `fetchJson` would collect the whole body and hand back one reply,
   * which is the very thing this endpoint exists not to do. flushHeaders sends the SSE head
   * before the first frame, and X-Accel-Buffering stops a proxy re-buffering what we just
   * took care not to buffer.
   */
  router.post('/text/stream', async (req, res) => {
    const text = String((req.body || {}).text || '').trim();
    if (!text) { res.status(400).json({ ok: false, error: 'text required' }); return; }
    const conversation_id = String((req.body || {}).conversation_id || '').slice(0, 200);
    const upstream: Record<string, unknown> = { text, conversation_id };
    // The client's id for this send (DEC-1791518421640932): the proxy validates it and replays a send it has seen.
    const turnId = (req.body || {}).turn_id;
    if (typeof turnId === 'string' && turnId) upstream.turn_id = turnId.slice(0, 64);
    for (const [key, pick] of [['brain', pickBrain], ['context', pickContext]] as const) {
      const raw = (req.body || {})[key];
      if (raw === undefined || raw === null) continue;
      const picked = pick(raw);
      if (!picked.ok) { res.status(400).json({ ok: false, ...picked.refusal }); return; }
      upstream[key] = picked.value;
    }
    const token = deps.token();
    if (!token) { res.status(502).json({ ok: false, error: 'gateway token unavailable' }); return; }
    if (!deps.pipeStream) { res.status(500).json({ ok: false, error: 'streaming unavailable' }); return; }
    try {
      const up = await deps.pipeStream(`${GATEWAY_URL}/arturo/text/stream`, {
        'Content-Type': 'application/json', 'Authorization': `Bearer ${token}`,
      }, JSON.stringify(upstream), 195000);
      if (up.status !== 200 || !up.contentType.includes('text/event-stream')) {
        // A refusal is ordinary JSON — the client never opened a stream, so answer like /text.
        res.status(up.status).json(await up.json());
        return;
      }
      res.status(200);
      res.setHeader('Content-Type', 'text/event-stream');
      res.setHeader('Cache-Control', 'no-cache');
      res.setHeader('X-Accel-Buffering', 'no');
      res.setHeader('Connection', 'keep-alive');
      res.flushHeaders?.();
      if (!up.body) { res.end(); return; }
      const reader = (up.body as ReadableStream<Uint8Array>).getReader();
      // The client going away must reach the CLI at the far end, not leak a subprocess.
      let aborted = false;
      res.on('close', () => { aborted = true; void reader.cancel().catch(() => {}); });
      for (;;) {
        const { done, value } = await reader.read();
        if (done || aborted) break;
        res.write(Buffer.from(value));
      }
      res.end();
    } catch (err: any) {
      if (res.headersSent) { res.end(); return; }
      res.status(502).json({ ok: false, error: 'gateway unreachable', detail: err.message });
    }
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
    // A disabled optional service is neither booting nor unreachable. Returning a
    // named state lets the home page stop its retry loop and tell the operator
    // what they configured, rather than showing "starting" forever.
    if (deps.enabled && !deps.enabled()) {
      res.status(503).json({ ok: false, error: 'arturo_disabled', disabled: true });
      return;
    }
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
    // ?turn=<id>: one send's fate (running | done | lost | unknown), the read-back after a dropped turn.
    const turn = req.query.turn;
    // ?jobs=1: the job tray's poll (this thread's running and just-finished background jobs).
    const suffix = typeof turn === 'string' && turn ? `?turn=${encodeURIComponent(turn.slice(0, 64))}`
      : req.query.jobs ? '?jobs=1' : '';
    await forward(res, `/arturo/threads/${id}${suffix}`, { method: 'GET' }, 10000);
  });

  // Stop a background job from the tray.
  router.post('/threads/:id/jobs/:jobId/cancel', async (req, res) => {
    const id = encodeURIComponent(String(req.params.id || '').slice(0, 200));
    const jobId = String(req.params.jobId || '');
    if (!id || !/^job_[0-9a-f]{6,32}$/.test(jobId)) { res.status(400).json({ ok: false, error: 'bad job id' }); return; }
    await forward(res, `/arturo/threads/${id}/jobs/${jobId}/cancel`, { method: 'POST' }, 10000);
  });

  // The voice picker: the same server rows and the same validated PUT the phone's settings use. The
  // provider keys stay on the server (its own key check decides what is available); only names,
  // ids and availability come back. Only the vendor and the voice id are passed on, never the
  // client's own fields, so the server's record of who changed it is its own.
  const vendorQuery = (raw: unknown) => {
    const v = typeof raw === 'string' ? raw : '';
    return /^[a-z0-9_-]{1,32}$/.test(v) ? `?vendor=${v}` : '';
  };
  router.get('/ptt/vendor', async (_req, res) => {
    await forward(res, '/arturo/ptt/vendor', { method: 'GET' }, 10000);
  });
  router.get('/ptt/voices', async (req, res) => {
    await forward(res, `/arturo/ptt/voices${vendorQuery(req.query.vendor)}`, { method: 'GET' }, 25000);
  });
  router.get('/ptt/voice', async (req, res) => {
    await forward(res, `/arturo/ptt/voice${vendorQuery(req.query.vendor)}`, { method: 'GET' }, 10000);
  });
  // Changing the voice changes it for everyone who talks to Arturo, so it is an admin's call (the
  // gateway itself only lets admin- or voice-scoped callers write it; this API holds a bearer with every
  // scope, so the check has to happen here). Reading stays open to anyone who can open the dashboard.
  router.put('/ptt/voice', async (req, res) => {
    const admin = deps.isAdmin ? deps.isAdmin(req) : tenantScope(req).isAdmin;
    if (!admin) {
      res.status(403).json({ ok: false, error: "Only an admin can change the assistant's voice" });
      return;
    }
    const { vendor, voice_id } = (req.body || {}) as Record<string, unknown>;
    if (typeof vendor !== 'string' || typeof voice_id !== 'string' || !vendor || !voice_id
        || vendor.length > 32 || voice_id.length > 200) {
      res.status(400).json({ ok: false, error: 'vendor and voice_id required' });
      return;
    }
    await forward(res, '/arturo/ptt/voice', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ vendor, voice_id }),
    }, 10000);
  });

  // Avatar looks, keyed by agent id (services/arturo/avatar_choice.py). Same split as the voice: anyone who
  // can open the dashboard reads them; only an admin changes or resets one, since a look shows on every
  // device. The values are checked here too, so a bad one never leaves this process, and only the known
  // fields are passed on.
  const isAdmin = (req: any) => (deps.isAdmin ? deps.isAdmin(req) : tenantScope(req).isAdmin);
  const agentParam = (raw: unknown) => {
    const v = typeof raw === 'string' ? raw : '';
    return AVATAR_AGENT_RE.test(v) ? v : null;
  };
  router.get('/avatars', async (_req, res) => {
    await forward(res, '/arturo/avatars', { method: 'GET' }, 10000);
  });
  router.get('/avatar', async (req, res) => {
    const agent = agentParam(req.query.agent);
    if (!agent) { res.status(400).json({ ok: false, error: 'agent required' }); return; }
    await forward(res, `/arturo/avatar?agent=${encodeURIComponent(agent)}`, { method: 'GET' }, 10000);
  });
  router.put('/avatar', async (req, res) => {
    if (!isAdmin(req)) { res.status(403).json({ ok: false, error: "Only an admin can change an agent's look" }); return; }
    const picked = pickAvatar(req.body);
    if (!picked.ok) { res.status(400).json({ ok: false, error: picked.error }); return; }
    await forward(res, '/arturo/avatar', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(picked.value),
    }, 10000);
  });
  router.delete('/avatar', async (req, res) => {
    if (!isAdmin(req)) { res.status(403).json({ ok: false, error: "Only an admin can change an agent's look" }); return; }
    const agent = agentParam(req.query.agent);
    if (!agent) { res.status(400).json({ ok: false, error: 'agent required' }); return; }
    await forward(res, `/arturo/avatar?agent=${encodeURIComponent(agent)}`, { method: 'DELETE' }, 10000);
  });

  // The assistant's name on this install ("Arturo" until renamed; the id stays "arturo"). Anyone reads it;
  // only an admin renames it, since it shows on every device and in the assistant's own instructions. Only
  // the name is passed on; Arturo's assistant_name.py decides what a valid name is.
  router.get('/name', async (_req, res) => {
    await forward(res, '/arturo/assistant/name', { method: 'GET' }, 10000);
  });
  router.put('/name', async (req, res) => {
    if (!isAdmin(req)) { res.status(403).json({ ok: false, error: "Only an admin can rename the assistant" }); return; }
    const name = req.body?.name;
    if (typeof name !== 'string' || !name.trim() || name.length > 64) { res.status(400).json({ ok: false, error: 'name required' }); return; }
    await forward(res, '/arturo/assistant/name', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
    }, 10000);
  });
  router.delete('/name', async (req, res) => {
    if (!isAdmin(req)) { res.status(403).json({ ok: false, error: "Only an admin can rename the assistant" }); return; }
    await forward(res, '/arturo/assistant/name', { method: 'DELETE' }, 10000);
  });

  return router;
}

// The allowed avatar values, as the dashboard's avatarCore.ts and Arturo's avatar_choice.py have them (the
// route test reads avatarCore.ts so these cannot drift).
export const AVATAR_SHAPES = ['circle', 'square', 'pill', 'hexagon', 'heart', 'droplet', 'star', 'pebble',
  'gumdrop', 'bean', 'cloud', 'bunny', 'kitty', 'egg', 'flower', 'sprout'] as const;
export const AVATAR_COLORS = ['ink', '#E5243B', '#FF7A1A', '#FFC61A', '#1FA855', '#00A6A0', '#1E6BE6',
  '#4B3FD8', '#8B2FE0', '#D61F8C', '#FF4F8B', '#2C2E3B'] as const;
export const AVATAR_EYES = ['auto', 'black', 'white'] as const;
const AVATAR_AGENT_RE = /^[A-Za-z0-9_.:-]{1,80}$/;

export type AvatarPick = { ok: true; value: { agent: string; shape?: string; color?: string; eyeColor?: string } } | { ok: false; error: string };
export function pickAvatar(raw: unknown): AvatarPick {
  if (!raw || typeof raw !== 'object') return { ok: false, error: 'a JSON body is required' };
  const r = raw as Record<string, unknown>;
  const agent = typeof r.agent === 'string' ? r.agent : '';
  if (!AVATAR_AGENT_RE.test(agent)) return { ok: false, error: 'agent required' };
  const out: { agent: string; shape?: string; color?: string; eyeColor?: string } = { agent };
  if (r.shape !== undefined) {
    if (typeof r.shape !== 'string' || !(AVATAR_SHAPES as readonly string[]).includes(r.shape)) return { ok: false, error: 'unknown shape' };
    out.shape = r.shape;
  }
  if (r.color !== undefined) {
    const c = typeof r.color === 'string' ? (r.color.toLowerCase() === 'ink' ? 'ink' : r.color.toUpperCase()) : '';
    if (!(AVATAR_COLORS as readonly string[]).includes(c)) return { ok: false, error: 'color must be ink or a Bold palette colour' };
    out.color = c;
  }
  if (r.eyeColor !== undefined) {
    if (typeof r.eyeColor !== 'string' || !(AVATAR_EYES as readonly string[]).includes(r.eyeColor)) return { ok: false, error: 'eyeColor must be auto, black or white' };
    out.eyeColor = r.eyeColor;
  }
  if (!out.shape && !out.color && !out.eyeColor) return { ok: false, error: 'nothing to change' };
  return { ok: true, value: out };
}

export default createArturoRouter();
