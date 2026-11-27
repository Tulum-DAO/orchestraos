/**
 * Native GPT-Live for the browser: the same call the iPhone app makes, through the watch gateway's
 * GET /arturo/gptlive/ws (scope ptt), instead of the relay engine (/api/voice/live -> gateway /live).
 *
 *   WS   /api/voice/native        browser <-> this process <-> gateway /arturo/gptlive/ws, frames verbatim both ways
 *                                 (text stays text, binary stays binary). The browser never holds the gateway bearer:
 *                                 this process dials with it (Authorization header; the principal is `fleet`).
 *   GET  /api/voice/native/ready  {ok, reason?}: does arturo's native service answer its health check, switched on?
 *                                 The page asks before a call and uses the relay engine when this says no.
 *   POST /api/voice/call-ended    the native server keeps no transcript, so the page posts the call's final turns
 *                                 here on hang-up; forwarded to the gateway's POST /voice-call-ended (scope ptt),
 *                                 which journals it as vc_client_<sha1(conv_id)[:12]> and answers {ok, call_id}.
 *
 * Browser auth for the socket is voice-live.ts's, unchanged (Origin guard, then cookie / JWT / Tailscale login from
 * loopback): both sockets are one implementation, bridgeBrowserSocket. The two HTTP routes take the same browser
 * auth, and ALSO a request combo-proxy (:5053) vouched for as an admin: an embedding app (an Arturo widget served from
 * another origin) reaches plain HTTP through combo, which authenticates the tailnet caller and replaces Tailscale-User-Login with its own x-orchestra-*
 * headers. A header alone proves nothing (the dashboard proxy forwards client headers as sent): the shared proxy
 * secret in x-orchestra-proxy-auth is checked timing-safe (lib/combo-vouch.ts), and x-orchestra-role must be admin.
 *
 * A refusal before the upgrade (the gateway answered an HTTP status instead of 101) reaches the page as a close code
 * 4000 + status, with a reason the page can show (4409 busy, 4503 switched off, ...): see nativeRejection.
 */
import { Router, type Request, type Response } from 'express';
import type { Server as HttpServer, IncomingMessage } from 'http';
import { WebSocket } from 'ws';
import { gatewayUrl, gatewayWsUrl } from '../lib/gateway-url.js';
import { comboProxyAdmin } from '../lib/combo-vouch.js';
import { bridgeBrowserSocket, browserIsAuthenticated, readGatewayToken } from './voice-live.js';

export const VOICE_NATIVE_PATH = '/api/voice/native';
export const NATIVE_UPSTREAM_PATH = '/arturo/gptlive/ws';

/** The close the page gets when the gateway refused the upgrade with `status`: 4000 + status, and a sentence. */
export function nativeRejection(status: number): { code: number; reason: string } {
  const s = Number.isInteger(status) && status >= 100 && status <= 999 ? status : 502;
  const reason =
    s === 409 ? 'busy: a call is already open'
    : s === 429 ? 'busy: too many calls are open'
    : s === 401 || s === 403 ? `refused: the voice gateway did not accept this server (HTTP ${s})`
    : s === 503 ? `unavailable: native voice is switched off (HTTP ${s})`
    : s === 502 ? `unavailable: the native voice service is not answering (HTTP ${s})`
    : `refused: the voice gateway answered HTTP ${s}`;
  return { code: 4000 + s, reason };
}

/** A close reason that fits the 123-byte limit, cut on a character boundary (never mid UTF-8 sequence). */
export function fitReason(reason: string, maxBytes = 123): string {
  let r = reason;
  while (Buffer.byteLength(r) > maxBytes) r = Array.from(r).slice(0, -1).join('');
  return r;
}

function closeQuiet(ws: WebSocket, code: number, reason: string) {
  try { ws.close(code, fitReason(reason)); } catch { /* already gone */ }
}

export function setupVoiceNativeWebSocket(httpServer: HttpServer, wsBase = gatewayWsUrl()) {
  bridgeBrowserSocket(httpServer, wsBase, {
    path: VOICE_NATIVE_PATH,
    // Arturo's own call only: no agent, no voice, no query (the gateway would forward a query to the native service).
    upstream: () => ({ path: NATIVE_UPSTREAM_PATH }),
    onUpstreamRejected: (clientWs, status) => {
      const r = nativeRejection(status);
      closeQuiet(clientWs, r.code, r.reason);
    },
    // The gateway's codes pass through (1000 "idle", 1001 "gateway restart", 1011 native dropped, ...). A code that
    // may not be sent (none: 1005, abnormal: 1006) becomes 1000 "call ended" / 1011 "voice service dropped".
    onUpstreamClosed: (clientWs, code, reason) => {
      if (!code || code === 1005) { closeQuiet(clientWs, 1000, reason || 'call ended'); return; }
      if (code === 1006) { closeQuiet(clientWs, 1011, 'voice service dropped'); return; }
      closeQuiet(clientWs, code, reason);
    },
    onUpstreamError: (clientWs, err) => closeQuiet(clientWs, 1011, `voice bridge upstream error: ${err.message}`),
  });
}

// ---- HTTP: auth -----------------------------------------------------------------------------------------------------

/** The socket's browser auth, or combo-proxy's secret-proven vouching for an admin (see the module doc). */
export function httpCallerAllowed(req: IncomingMessage): boolean {
  return browserIsAuthenticated(req) || comboProxyAdmin(req);
}

// ---- GET /native/ready ----------------------------------------------------------------------------------------------

export interface NativeReadyDeps {
  hasGatewayToken: () => boolean;
  /** arturo native's /native/healthz body, or 'down' (refused, non-2xx, bad JSON) / 'timeout'. */
  health: () => Promise<{ ok?: unknown; enabled?: unknown } | 'down' | 'timeout'>;
}

export const NATIVE_NOT_SET_UP = "Voice calls aren't set up on this server";
export const NATIVE_OFF = 'Native voice is switched off';
export const NATIVE_DOWN = 'The native voice service is not answering';
export const NATIVE_SLOW = 'The native voice service did not answer in time';

export async function voiceNativeReady(deps: NativeReadyDeps): Promise<{ ok: boolean; reason?: string }> {
  if (!deps.hasGatewayToken()) return { ok: false, reason: NATIVE_NOT_SET_UP };
  const h = await deps.health();
  if (h === 'timeout') return { ok: false, reason: NATIVE_SLOW };
  if (h === 'down' || h.ok !== true) return { ok: false, reason: NATIVE_DOWN };
  if (h.enabled === false) return { ok: false, reason: NATIVE_OFF };
  return { ok: true };
}

export function nativeHealthUrl(env: NodeJS.ProcessEnv = process.env): string {
  const port = Number(env.ORCHESTRA_ARTURO_NATIVE_PORT || 5076);
  return `http://127.0.0.1:${Number.isInteger(port) && port > 0 && port < 65536 ? port : 5076}/native/healthz`;
}

export async function fetchNativeHealth(url = nativeHealthUrl(), timeoutMs = 2500): Promise<{ ok?: unknown; enabled?: unknown } | 'down' | 'timeout'> {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
    if (!r.ok) return 'down';
    const d = await r.json().catch(() => null);
    return d && typeof d === 'object' ? d as { ok?: unknown; enabled?: unknown } : 'down';
  } catch (e) {
    const name = (e as { name?: string })?.name;
    return name === 'TimeoutError' || name === 'AbortError' ? 'timeout' : 'down';
  }
}

export const realNativeReadyDeps: NativeReadyDeps = {
  hasGatewayToken: () => !!readGatewayToken(),
  health: () => fetchNativeHealth(),
};

// ---- POST /call-ended -----------------------------------------------------------------------------------------------

/** The gateway's own limits (watch_gateway.py handle_voice_call_ended): 200 turns, 2000 characters a turn. A longer
 *  call keeps its NEWEST 200 turns (the gateway would keep the first 200), and a longer line is cut. */
export const MAX_TURNS = 200;
export const MAX_TEXT = 2000;
export const MAX_BODY_BYTES = 1024 * 1024;
/** ended_at must be "now" give or take this (a journal for a call that ended long ago, or in the future, is not a
 *  hang-up), and a call is at most MAX_CALL_S long. */
export const CLOCK_SKEW_S = 5 * 60;
export const MAX_CALL_S = 4 * 60 * 60;
const CONV_ID_RE = /^native-[A-Za-z0-9_-]{1,64}$/;
/** The Arturo thread the call was started from (optional): the call's card is written there. Same shape the gateway
 *  accepts (scripts/watch_gateway.py VOICE_THREAD_ID_RE); the page only sends one that matches. */
export const THREAD_ID_RE = /^[A-Za-z0-9_.:-]{1,80}$/;

export interface CallEndedTurn { role: 'user' | 'arturo'; text: string; ts: number | null }
export interface CallEndedBody { started_at: number; ended_at: number; turns: CallEndedTurn[]; conv_id: string; thread_id?: string }

/** The body as the gateway gets it, or the reason it is refused (400, or 413 when too big). */
export function validateCallEnded(body: unknown, nowS = Date.now() / 1000): { ok: true; body: CallEndedBody } | { ok: false; status: number; error: string } {
  const bad = (error: string, status = 400) => ({ ok: false as const, status, error });
  if (!body || typeof body !== 'object' || Array.isArray(body)) return bad('body must be a JSON object');
  let size = 0;
  try { size = Buffer.byteLength(JSON.stringify(body)); } catch { return bad('body is not serialisable'); }
  if (size > MAX_BODY_BYTES) return bad(`body over ${MAX_BODY_BYTES} bytes`, 413);
  const b = body as Record<string, unknown>;
  const fin = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
  if (!fin(b.started_at) || b.started_at <= 0) return bad('started_at required (epoch seconds > 0)');
  if (!fin(b.ended_at) || b.ended_at < b.started_at) return bad('ended_at required (epoch seconds, not before started_at)');
  if (Math.abs(b.ended_at - nowS) > CLOCK_SKEW_S) return bad(`ended_at must be within ${CLOCK_SKEW_S / 60} minutes of now`);
  if (b.ended_at - b.started_at > MAX_CALL_S) return bad(`a call is at most ${MAX_CALL_S / 3600} hours long`);
  if (typeof b.conv_id !== 'string' || !CONV_ID_RE.test(b.conv_id)) return bad('conv_id must be "native-<id>"');
  // Optional, and refused like every other malformed field (the page never sends a malformed one): absent or null
  // is no thread.
  if (b.thread_id !== undefined && b.thread_id !== null && (typeof b.thread_id !== 'string' || !THREAD_ID_RE.test(b.thread_id))) {
    return bad('thread_id must be 1-80 of A-Z a-z 0-9 _ . : -');
  }
  if (!Array.isArray(b.turns)) return bad('turns must be a list');
  const turns: CallEndedTurn[] = [];
  for (const [i, t] of b.turns.slice(-MAX_TURNS).entries()) {
    if (!t || typeof t !== 'object') return bad(`turns[${i}] must be an object`);
    const { role, text, ts } = t as Record<string, unknown>;
    if (role !== 'user' && role !== 'arturo') return bad(`turns[${i}].role must be user or arturo`);
    if (typeof text !== 'string') return bad(`turns[${i}].text must be a string`);
    if (ts !== undefined && ts !== null && !fin(ts)) return bad(`turns[${i}].ts must be a number`);
    turns.push({ role, text: Array.from(text).slice(0, MAX_TEXT).join(''), ts: fin(ts) ? ts : null });
  }
  return { ok: true, body: { started_at: b.started_at, ended_at: b.ended_at, turns, conv_id: b.conv_id,
    ...(typeof b.thread_id === 'string' ? { thread_id: b.thread_id } : {}) } };
}

/** conv_ids already journaled by this process, for CONV_TTL_MS: a repeat post is a 409, never a second journal. A
 *  post the gateway refused (or never got) is forgotten again, so the page may retry it. */
export const CONV_TTL_MS = 6 * 60 * 60 * 1000;
export class ConvSeen {
  private readonly seen = new Map<string, number>();
  constructor(private readonly ttlMs = CONV_TTL_MS, private readonly now = () => Date.now()) {}
  /** true if newly claimed; false if claimed within the TTL. */
  claim(id: string): boolean {
    const t = this.now();
    for (const [k, at] of this.seen) if (t - at > this.ttlMs) this.seen.delete(k);
    if (this.seen.has(id)) return false;
    this.seen.set(id, t);
    return true;
  }
  release(id: string): void { this.seen.delete(id); }
}

export interface CallEndedDeps {
  token: () => string;
  /** POST the body to the gateway's /voice-call-ended; its status and JSON. */
  post: (body: CallEndedBody, token: string) => Promise<{ status: number; json: unknown }>;
}

async function realPost(body: CallEndedBody, token: string): Promise<{ status: number; json: unknown }> {
  const r = await fetch(`${gatewayUrl()}/voice-call-ended`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(8000),
  });
  const json = await r.json().catch(() => ({ ok: false, error: `gateway answered HTTP ${r.status}` }));
  return { status: r.status, json };
}

export const realCallEndedDeps: CallEndedDeps = { token: readGatewayToken, post: realPost };

/** Mounted at /api/voice. */
export function createVoiceNativeRouter(
  ready: NativeReadyDeps = realNativeReadyDeps, ended: CallEndedDeps = realCallEndedDeps, seen = new ConvSeen(),
): Router {
  const router = Router();
  router.get('/native/ready', async (req: Request, res: Response) => {
    if (!httpCallerAllowed(req)) { res.status(401).json({ ok: false, reason: 'not signed in' }); return; }
    try { res.json(await voiceNativeReady(ready)); }
    catch { res.json({ ok: false, reason: NATIVE_DOWN }); }
  });
  router.post('/call-ended', async (req: Request, res: Response) => {
    if (!httpCallerAllowed(req)) { res.status(401).json({ ok: false, error: 'not signed in' }); return; }
    const v = validateCallEnded(req.body);
    if (!v.ok) { res.status(v.status).json({ ok: false, error: v.error }); return; }
    const token = ended.token();
    if (!token) { res.status(502).json({ ok: false, error: NATIVE_NOT_SET_UP }); return; }
    if (!seen.claim(v.body.conv_id)) { res.status(409).json({ ok: false, error: 'this call was already saved' }); return; }
    try {
      const r = await ended.post(v.body, token);
      if (r.status < 200 || r.status > 299) seen.release(v.body.conv_id);
      res.status(r.status >= 100 && r.status <= 599 ? r.status : 502).json(r.json);
    } catch {
      seen.release(v.body.conv_id);
      res.status(502).json({ ok: false, error: 'gateway unreachable' });
    }
  });
  return router;
}
