/**
 * B3 voice bridge — a NEW WebSocket proxy: browser <-> this Node process <->
 * watch_gateway.py `/live` (Gemini Live bidi: 16kHz PCM up / 24kHz PCM
 * down / JSON transcript events). The browser NEVER holds the gateway bearer
 * token and NEVER reaches the gateway directly — this file is the only place that
 * reads that token and the only socket that dials out to it (lib/gateway-url.ts:
 * env, then [gateway] in orchestra.toml).
 * It does not touch watch_gateway.py's own `_authorized`/`handle_gemini_live`
 * gate; it satisfies that gate as a trusted, same-box loopback caller holding
 * the real bearer — the same pattern routes/voice.ts already uses for
 * GET /api/voice/call (GATEWAY_TOKEN_FILE + Authorization: Bearer).
 *
 * WHY prependListener, not `new WebSocketServer({server, path})`:
 * api/src/server.ts already owns one path-bound WebSocketServer
 * (`/ws/terminal`, see terminal.ts) on the SAME underlying http.Server. The
 * `ws` library's internals (node_modules/ws/lib/websocket-server.js) attach
 * an UNCONDITIONAL `server.on('upgrade', ...)` listener per instance that
 * calls `handleUpgrade`, which 400-aborts (destroys the socket) for any
 * upgrade whose path doesn't match ITS OWN `options.path` — regardless of
 * whether another listener would have handled it. A second
 * `new WebSocketServer({server, path: '/api/voice/live'})` registered the
 * normal way would race the terminal WSS and typically lose (registered
 * later == closer to when this route's own construction runs). Using
 * `server.prependListener('upgrade', ...)` here guarantees THIS listener
 * always runs first; it checks the pathname itself and does nothing (a
 * pure fall-through, no socket.destroy()) for anything that isn't
 * `/api/voice/live`, leaving `/ws/terminal` (and everything else) exactly as
 * it was.
 *
 * WIRING (server.ts is NOT edited by this task — file-ownership rule; report
 * only). Add, near the existing `setupTerminalWebSocket(wss)` call:
 *   import { setupVoiceLiveWebSocket } from './routes/voice-live.js';
 *   setupVoiceLiveWebSocket(server);
 */
import { readFileSync } from 'fs';
import { join } from 'path';
import { Router, type Request, type Response } from 'express';
import { createHmac } from 'crypto';
import { WebSocketServer, WebSocket } from 'ws';
import type { Server as HttpServer, IncomingMessage } from 'http';
import type { Socket } from 'net';

// Read fresh (not captured once at module-load) so tests can point this at a
// fixture file via env without import-order games, and so a real deployment
// that sets the env after other modules load still picks it up correctly.
import { gatewayTokenFile } from '../lib/gateway-token.js';
import { sessionSecret, jwtSecret } from '../lib/shared-secret.js';  // #85: shared reader
import { gatewayWsUrl, gatewayUrl } from '../lib/gateway-url.js';
import { dataDir } from '../lib/config.js';
import { tailscaleOperator, isLoopback } from '../lib/tailscale-identity.js';
import { comboProxyVouched, comboProxyAdmin } from '../lib/combo-vouch.js';
import { isSafeAgentId } from '../lib/agentPaths.js';
import { principal } from '../lib/principal.js';
import { canPrincipalSeeAgent, defaultResolveTags } from '../lib/agent-scope.js';
const GATEWAY_WS_URL = gatewayWsUrl();  // #84 class: env, then [gateway] in orchestra.toml
export const VOICE_LIVE_PATH = '/api/voice/live';

function getCookie(req: IncomingMessage, name: string): string | null {
  const c = req.headers.cookie || '';
  const m = c.match(new RegExp('(?:^|;)\\s*' + name + '=([^;]+)'));
  return m ? decodeURIComponent(m[1]) : null;
}

/** How one credential fared: present and valid, absent, present but failing its signature/shape, or expired. */
export type CredentialCheck = 'ok' | 'absent' | 'invalid' | 'expired';
/** The Tailscale identity Serve stamps: ok, no login header, a login but not from loopback (so not Serve's), or a
 *  login that is not on the allowlist. */
export type TailscaleCheck = 'ok' | 'absent' | 'not-loopback' | 'not-allowed';
/** The front login proxy's vouch (lib/combo-vouch): ok, no vouch headers, a wrong secret or non-loopback peer, or a
 *  vouched user whose role is not admin. */
export type ComboCheck = 'ok' | 'absent' | 'invalid' | 'not-admin';

export interface BrowserAuth {
  ok: boolean;
  /** Who, when any credential names someone: the session's user, the token's subject or the Tailscale login. */
  user: string | null;
  /** The credential that let the request in. */
  via: 'cookie' | 'token' | 'tailscale' | 'combo' | null;
  cookie: CredentialCheck;
  token: CredentialCheck;
  tailscale: TailscaleCheck;
  combo: ComboCheck;
}

// Same HMAC scheme as combo-proxy.js's makeSessionToken/verifySessionToken —
// the `orchestra_session` cookie that already gates every page, and every
// other WS (/ws/terminal), in front of this app. Duplicated here (not
// imported) per the file-ownership rule: combo-proxy.js is not edited and
// exports nothing importable.
function checkOrchestraSession(token: string | null): { check: CredentialCheck; user: string | null } {
  if (!token) return { check: 'absent', user: null };
  const dot = token.indexOf('.');
  if (dot < 0) return { check: 'invalid', user: null };
  const b64 = token.slice(0, dot);
  const sig = token.slice(dot + 1);
  const expected = createHmac('sha256', sessionSecret()).update(b64).digest('base64url');
  if (sig !== expected) return { check: 'invalid', user: null };
  try {
    const payload = JSON.parse(Buffer.from(b64, 'base64url').toString());
    // A name is reported only once the credential has verified: an invalid or expired payload is a claim.
    const user = typeof payload.u === 'string' && payload.u ? payload.u : null;
    if (typeof payload.exp !== 'number') return { check: 'invalid', user: null };
    return payload.exp >= Date.now() ? { check: 'ok', user } : { check: 'expired', user: null };
  } catch {
    return { check: 'invalid', user: null };
  }
}

// Same JWT scheme as routes/token-auth.ts's verifyJwt (Bearer header or the
// `orchestra_token` cookie) — accepted as an alternate valid session (the
// multi-tenant login path uses this one instead of the VPS cookie).
function checkOrchestraToken(req: IncomingMessage): { check: CredentialCheck; user: string | null } {
  let token: string | null = null;
  const auth = req.headers.authorization;
  if (auth?.startsWith('Bearer ')) token = auth.slice(7);
  if (!token) token = getCookie(req, 'orchestra_token');
  if (!token) return { check: 'absent', user: null };
  const parts = token.split('.');
  if (parts.length !== 3) return { check: 'invalid', user: null };
  const [header, body, sig] = parts;
  const expected = createHmac('sha256', jwtSecret()).update(`${header}.${body}`).digest('base64url');
  if (sig !== expected) return { check: 'invalid', user: null };
  try {
    const payload = JSON.parse(Buffer.from(body, 'base64url').toString());
    const user = typeof payload.sub === 'string' && payload.sub ? payload.sub : null;
    return !payload.exp || payload.exp >= Math.floor(Date.now() / 1000) ? { check: 'ok', user } : { check: 'expired', user: null };
  } catch {
    return { check: 'invalid', user: null };
  }
}

function checkTailscale(req: IncomingMessage): { check: TailscaleCheck; user: string | null } {
  const login = String(req.headers['tailscale-user-login'] || '').trim().toLowerCase();
  if (!login) return { check: 'absent', user: null };
  // The login header is a claim until Serve's loopback peer and the allowlist both agree: only then is it a name.
  if (!isLoopback(req.socket?.remoteAddress)) return { check: 'not-loopback', user: null };
  return tailscaleOperator(req) ? { check: 'ok', user: login } : { check: 'not-allowed', user: null };
}

/** The front login proxy admits a tailnet user with no cookie and vouches on the upgrade with its shared secret plus
 *  x-orchestra-user / x-orchestra-role (the same predicate the /ready routes accept). Loopback + secret + admin. */
function checkCombo(req: IncomingMessage): { check: ComboCheck; user: string | null } {
  const h = (n: string) => { const v = req.headers[n]; return String((Array.isArray(v) ? v[0] : v) || '').trim(); };
  if (!h('x-orchestra-proxy-auth') && !h('x-orchestra-user')) return { check: 'absent', user: null };
  if (!comboProxyVouched(req)) return { check: 'invalid', user: null };
  return comboProxyAdmin(req) ? { check: 'ok', user: h('x-orchestra-user') } : { check: 'not-admin', user: null };
}

/** The browser's credentials, in the order they are accepted (cookie, then JWT, then Tailscale login, then the login
 *  proxy's vouch); the checks stop at the first that passes, and a refusal reports all four, so the log can say what
 *  was missing. */
export function browserAuth(req: IncomingMessage): BrowserAuth {
  const out: BrowserAuth = { ok: false, user: null, via: null, cookie: 'absent', token: 'absent', tailscale: 'absent', combo: 'absent' };
  const cookie = checkOrchestraSession(getCookie(req, 'orchestra_session'));
  out.cookie = cookie.check;
  out.user = cookie.user;
  if (cookie.check === 'ok') return { ...out, ok: true, via: 'cookie' };
  const token = checkOrchestraToken(req);
  out.token = token.check;
  out.user = out.user ?? token.user;
  if (token.check === 'ok') return { ...out, ok: true, via: 'token', user: token.user ?? out.user };
  const ts = checkTailscale(req);
  out.tailscale = ts.check;
  out.user = out.user ?? ts.user;
  if (ts.check === 'ok') return { ...out, ok: true, via: 'tailscale', user: ts.user ?? out.user };
  const combo = checkCombo(req);
  out.combo = combo.check;
  if (combo.check === 'ok') return { ...out, ok: true, via: 'combo', user: combo.user };
  return out;
}

export function browserIsAuthenticated(req: IncomingMessage): boolean {
  return browserAuth(req).ok;
}

/**
 * CSWSH guard (security-review HIGH finding on commit 4840bf6247): a cookie
 * alone does not prove the request came from OUR page — any site can open a
 * cross-site WebSocket and the browser will attach cookies automatically.
 * Require Origin to be present and either same-host as this request, or in
 * an explicit allowlist (VOICE_LIVE_ALLOWED_ORIGINS, comma-separated). This
 * MUST run before browserIsAuthenticated so a cross-site probe gets a flat
 * 403 with no signal about whether its cookie/token would have passed.
 */
export function isAllowedOrigin(origin: string | undefined, host: string | undefined): boolean {
  if (!origin) return false;
  let originHost: string;
  try {
    originHost = new URL(origin).host;
  } catch {
    return false;
  }
  if (host && originHost === host) return true;
  const allowlist = (process.env.VOICE_LIVE_ALLOWED_ORIGINS || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  return allowlist.includes(origin) || allowlist.includes(originHost);
}

export function readGatewayToken(): string {
  try {
    return readFileSync(gatewayTokenFile(), 'utf-8').trim();
  } catch {
    return '';
  }
}

/** W1: fail LOUD, never a silent drop — push one JSON reason frame before closing. */
function closeLoud(ws: WebSocket, code: number, reason: string) {
  try { ws.send(JSON.stringify({ event: 'error', reason })); } catch { /* already gone */ }
  try { ws.close(code, reason.slice(0, 123)); } catch { /* already gone */ }
}

/** What differs between the browser sockets this file bridges to the gateway (relay /live, native /arturo/gptlive/ws).
 *  Everything else (the CSWSH Origin guard, browser auth, the bearer check, buffering frames until the upstream
 *  opens, verbatim passthrough both ways) is one implementation: bridgeBrowserSocket below. */
export interface BrowserBridgeSpec {
  /** The browser-facing path this bridge owns (exact pathname match). */
  path: string;
  /** After Origin + auth, before the bearer is read: the upstream path (+query) to dial, or a raw HTTP refusal. */
  upstream: (url: URL, req: IncomingMessage) => { path: string } | { refuse: string };
  /** The gateway answered the upgrade with an HTTP status instead of 101. */
  onUpstreamRejected: (clientWs: WebSocket, status: number) => void;
  /** The gateway's socket closed while the browser's is still open. */
  onUpstreamClosed: (clientWs: WebSocket, code: number, reason: string) => void;
  /** The upstream socket errored while the browser's is still open. */
  onUpstreamError: (clientWs: WebSocket, err: Error) => void;
  /** Tests only: a shorter upstream handshake timeout than UPSTREAM_HANDSHAKE_MS. */
  handshakeMs?: number;
}

/** The upstream gets this long to answer the upgrade before the browser is told the bridge failed. */
export const UPSTREAM_HANDSHAKE_MS = 10_000;
/** Frames held for an upstream that has not opened yet: past this the browser is closed 1009 (too big), so a
 *  stalled upstream can never make this process buffer a stream of mic audio without bound. */
export const MAX_PENDING_BYTES = 2 * 1024 * 1024;

/** One line per WebSocket outcome, in the API's log (console, like lib/access-log.ts): `[voice-ws <iso>] k=v ...`.
 *  Values with whitespace or quotes are JSON-quoted; empty values are left out. Never a credential: callers pass
 *  outcomes, statuses, hosts and logins, and the bearer, cookie and JWT values never reach this function. */
export function voiceWsLog(fields: Record<string, string | number | null | undefined>): void {
  const bits: string[] = [];
  for (const [k, v] of Object.entries(fields)) {
    if (v === null || v === undefined || v === '') continue;
    const str = String(v);
    bits.push(`${k}=${/[\s"\\]/.test(str) ? JSON.stringify(str) : str}`);
  }
  console.log(`[voice-ws ${new Date().toISOString()}] ${bits.join(' ')}`);
}

/** The upstream as the log names it: scheme and host (never userinfo) plus the dialed path. */
function upstreamForLog(gatewayWsUrl: string, path: string): string {
  try {
    const u = new URL(gatewayWsUrl);
    return `${u.protocol}//${u.host}${path}`;
  } catch {
    return `<unparseable gateway url>${path}`;
  }
}

export function bridgeBrowserSocket(httpServer: HttpServer, gatewayWsUrl: string, spec: BrowserBridgeSpec) {
  const wss = new WebSocketServer({ noServer: true });

  httpServer.prependListener('upgrade', (req: IncomingMessage, socket: Socket, head: Buffer) => {
    const url = new URL(req.url || '/', 'http://internal');
    if (url.pathname !== spec.path) return; // not ours — fall through untouched (e.g. /ws/terminal)

    // Every exit below leaves exactly one line: the path, who (when any credential names someone), the requester,
    // the outcome and its particulars. An upgrade never passes the Express access log, so this is the only trace.
    const startedAt = Date.now();
    const xff = req.headers['x-forwarded-for'];
    const who: Record<string, string | number | null | undefined> = {
      path: spec.path,
      user: null,
      addr: req.socket?.remoteAddress ?? null,
      'x-forwarded-for(claimed)': xff ? (Array.isArray(xff) ? xff.join(',') : String(xff)) : null,
    };
    const log = (outcome: string, extra: Record<string, string | number | null | undefined> = {}) =>
      voiceWsLog({ ...who, outcome, ...extra });

    try {
      if (!isAllowedOrigin(req.headers.origin, req.headers.host)) {
        // CSWSH guard runs BEFORE auth — no auth oracle for cross-site callers.
        log('refused-origin', { status: 403, origin: req.headers.origin || 'absent', host: req.headers.host || 'absent' });
        socket.write('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
        socket.destroy();
        return;
      }

      const auth = browserAuth(req);
      who.user = auth.user;
      if (!auth.ok) {
        log('refused-auth', { status: 401, reason: `cookie:${auth.cookie},token:${auth.token},tailscale:${auth.tailscale},combo:${auth.combo}` });
        socket.write('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n');
        socket.destroy();
        return;
      }

      const target = spec.upstream(url, req);
      if ('refuse' in target) {
        const status = Number(/^HTTP\/1\.1 (\d{3})/.exec(target.refuse)?.[1]) || 0;
        const body = target.refuse.split('\r\n\r\n')[1] || '';
        log('refused-request', { status, reason: body.slice(0, 120) });
        socket.write(target.refuse);
        socket.destroy();
        return;
      }

      const gatewayToken = readGatewayToken();
      if (!gatewayToken) {
        // W1: LOUD failure at the HTTP-upgrade layer (before any WS handshake)
        // rather than a hung/silent connection.
        log('refused-no-bearer', { status: 502 });
        socket.write(
          'HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n'
          + `voice bridge unavailable: watch-gateway bearer token missing at ${gatewayTokenFile()}`,
        );
        socket.destroy();
        return;
      }

      // ws answers a malformed handshake (no Sec-WebSocket-Key, wrong version, ...) with 400 and destroys the socket
      // without ever calling back: the socket closing before the callback ran is that outcome.
      let accepted = false;
      socket.once('close', () => { if (!accepted) log('aborted-handshake', { status: 400 }); });

      wss.handleUpgrade(req, socket, head, (clientWs) => {
        accepted = true;
        // MEDIUM finding (security review, commit 4840bf6247): the gateway
        // bearer must never appear in a URL (query strings land in access
        // logs, proxy logs, etc.). Send it as an Authorization header instead
        // — the same pattern routes/voice.ts already uses for /voice-call.
        const upstream = new WebSocket(
          `${gatewayWsUrl}${target.path}`,
          { headers: { Authorization: `Bearer ${gatewayToken}` }, handshakeTimeout: spec.handshakeMs ?? UPSTREAM_HANDSHAKE_MS },
        );
        log('dialed', { via: auth.via, upstream: upstreamForLog(gatewayWsUrl, target.path) });

        let upstreamOpen = false;
        let upstreamFailed = false;   // one upstream-refused line, whether it was an HTTP status or a socket error
        const pending: Array<{ data: any; isBinary: boolean }> = [];
        let pendingBytes = 0;

        upstream.on('open', () => {
          upstreamOpen = true;
          for (const m of pending.splice(0)) upstream.send(m.data, { binary: m.isBinary });
          pendingBytes = 0;
        });

        upstream.on('unexpected-response', (_req, res) => {
          if (!upstreamFailed) { upstreamFailed = true; log('upstream-refused', { status: res.statusCode || 0 }); }
          spec.onUpstreamRejected(clientWs, res.statusCode || 0);
        });

        upstream.on('message', (data, isBinary) => {
          if (clientWs.readyState === WebSocket.OPEN) clientWs.send(data as any, { binary: isBinary });
        });

        upstream.on('close', (code, reasonBuf) => {
          if (clientWs.readyState !== WebSocket.OPEN) return;
          spec.onUpstreamClosed(clientWs, code, reasonBuf?.toString() || '');
        });

        upstream.on('error', (err) => {
          if (!upstreamFailed && !upstreamOpen) {
            upstreamFailed = true;
            log('upstream-refused', { error: (err as NodeJS.ErrnoException).code || err.message });
          }
          if (clientWs.readyState === WebSocket.OPEN) spec.onUpstreamError(clientWs, err);
        });

        clientWs.on('message', (data, isBinary) => {
          if (upstreamOpen && upstream.readyState === WebSocket.OPEN) {
            upstream.send(data as any, { binary: isBinary });
          } else {
            pendingBytes += (data as Buffer).length ?? 0;
            if (pendingBytes > MAX_PENDING_BYTES) {
              pending.length = 0;
              try { clientWs.close(1009, 'too much audio before the voice service answered'); } catch { /* gone */ }
              try { upstream.terminate(); } catch { /* noop */ }
              return;
            }
            pending.push({ data, isBinary });
          }
        });

        clientWs.on('close', (code, reasonBuf) => {
          log('closed', { code, reason: reasonBuf?.toString() || '', ms: Date.now() - startedAt });
          try { upstream.close(); } catch { /* noop */ }
        });
        clientWs.on('error', () => { try { upstream.close(); } catch { /* noop */ } });
      });
    } catch (e) {
      // A throw here would otherwise be an uncaught exception from the http server's 'upgrade' emit: say what it was,
      // answer the browser, and never leave the socket hanging.
      log('error', { status: 500, error: e instanceof Error ? e.message : String(e) });
      try { socket.write('HTTP/1.1 500 Internal Server Error\r\nConnection: close\r\n\r\n'); } catch { /* gone */ }
      socket.destroy();
    }
  });
}

export function setupVoiceLiveWebSocket(httpServer: HttpServer, gatewayWsUrl = GATEWAY_WS_URL) {
  bridgeBrowserSocket(httpServer, gatewayWsUrl, {
    path: VOICE_LIVE_PATH,
    upstream: (url, req) => {
      // The agent the call is with, when the page names one: a safe id the caller may see, by the same scope rule as
      // every /api/agents/:id route (an out-of-scope id gets the same 404 an unknown one does). None: Arturo's own call.
      const agent = url.searchParams.get('agent');
      if (agent !== null) {
        if (!isSafeAgentId(agent)) {
          return { refuse: 'HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\nbad agent id' };
        }
        if (!canPrincipalSeeAgent(principal(req as any), { id: agent, tags: defaultResolveTags(agent) })) {
          return { refuse: 'HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\nagent not found' };
        }
      }
      const voice = url.searchParams.get('voice') || 'Fenrir';
      return { path: `/live?voice=${encodeURIComponent(voice)}${agent !== null ? `&agent=${encodeURIComponent(agent)}` : ''}` };
    },
    onUpstreamRejected: (clientWs, status) => {
      closeLoud(clientWs, 1011, `voice bridge upstream rejected the connection (HTTP ${status})`);
    },
    onUpstreamClosed: (clientWs, code, reason) => {
      // No close code (1005 none sent, 1006 dropped): a code the browser side may not be sent, so passing it on threw
      // and left the page's call open. That is the call ending: say so plainly. A real code passes through.
      if (!code || code === 1005 || code === 1006) {
        try { clientWs.close(1000, 'call ended'); } catch { /* already gone */ }
        return;
      }
      closeLoud(clientWs, code, reason || 'gateway closed the voice session');
    },
    onUpstreamError: (clientWs, err) => {
      closeLoud(clientWs, 1011, `voice bridge upstream error: ${err.message}`);
    },
  });
}

// ---- GET /api/voice/live/ready ------------------------------------------------------------------------------------
// Read-only: will a call on /api/voice/live start? The page asks before it offers Call, so Call is never offered where
// it cannot work. It never places a call, never opens a realtime session, and never returns or logs a key: it only
// asks whether one exists. The answer is the install's, not an agent's: the bridge above sends no agent to the
// gateway's /live, so every agent's call runs the same engine with the same key.

export type LiveEngine = 'gemini' | 'relay' | null | 'unreachable';

export interface VoiceReadyDeps {
  /** Whether the gateway bearer exists (the bridge refuses with 502 without it). */
  hasGatewayToken: () => boolean;
  /** The engine the gateway will run, from Arturo's /health live_engine, or 'unreachable'. */
  liveEngine: () => Promise<LiveEngine>;
  /** Whether a Gemini key is set where the gateway's Live bridge reads it. */
  hasGeminiKey: () => boolean;
}

export const NOT_SET_UP = "Voice calls aren't set up on this server";
export const NO_KEY = 'No voice key is set up';

export async function voiceLiveReady(deps: VoiceReadyDeps): Promise<{ ok: boolean; reason?: string }> {
  if (!deps.hasGatewayToken()) return { ok: false, reason: NOT_SET_UP };
  const engine = await deps.liveEngine();
  if (engine === 'gemini' || engine === 'relay') return { ok: true };
  // The gateway runs Gemini when it cannot ask Arturo, so then only a Gemini key makes a call start.
  if (engine === 'unreachable' && deps.hasGeminiKey()) return { ok: true };
  return { ok: false, reason: NO_KEY };
}

/** Presence only, as services/arturo/gemini_live_bridge.py get_gemini_api_key looks: the data dir's .env.secrets, then
 *  the environment. The value is never kept, returned or logged. */
function realHasGeminiKey(): boolean {
  try {
    const text = readFileSync(join(dataDir(), '.env.secrets'), 'utf-8');
    if (text.split('\n').some((l) => /^GEMINI_API_KEY=\S/.test(l))) return true;
  } catch { /* no secrets file */ }
  return !!(process.env.GEMINI_API_KEY || '').trim();
}

async function realLiveEngine(): Promise<LiveEngine> {
  try {
    const r = await fetch(`${gatewayUrl()}/arturo/health`, {
      headers: { Authorization: `Bearer ${readGatewayToken()}` }, signal: AbortSignal.timeout(4000),
    });
    if (!r.ok) return 'unreachable';
    const e = (await r.json() as { live_engine?: unknown })?.live_engine;
    return e === 'gemini' || e === 'relay' ? e : e === null ? null : 'unreachable';
  } catch { return 'unreachable'; }
}

export const realVoiceReadyDeps: VoiceReadyDeps = {
  hasGatewayToken: () => !!readGatewayToken(),
  liveEngine: realLiveEngine,
  hasGeminiKey: realHasGeminiKey,
};

/** Mounted at /api/voice. */
export function createVoiceLiveReadyRouter(deps: VoiceReadyDeps = realVoiceReadyDeps): Router {
  const router = Router();
  router.get('/live/ready', async (_req: Request, res: Response) => {
    try { res.json(await voiceLiveReady(deps)); }
    catch { res.json({ ok: false, reason: NOT_SET_UP }); }
  });
  return router;
}
