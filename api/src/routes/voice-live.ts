/**
 * B3 voice bridge — a NEW WebSocket proxy: browser <-> this Node process <->
 * watch_gateway.py:9091 `/live` (Gemini Live bidi: 16kHz PCM up / 24kHz PCM
 * down / JSON transcript events). The browser NEVER holds the gateway bearer
 * token and NEVER reaches :9091 directly — this file is the only place that
 * reads that token and the only socket that dials out to 127.0.0.1:9091.
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
import { createHmac } from 'crypto';
import { WebSocketServer, WebSocket } from 'ws';
import type { Server as HttpServer, IncomingMessage } from 'http';
import type { Socket } from 'net';

const SESSION_SECRET = process.env.SESSION_SECRET || 'orchestraOS-session-2026';
const JWT_SECRET = process.env.JWT_SECRET || 'orchestraOS-jwt-secret-2026';
// Read fresh (not captured once at module-load) so tests can point this at a
// fixture file via env without import-order games, and so a real deployment
// that sets the env after other modules load still picks it up correctly.
import { gatewayTokenFile } from '../lib/gateway-token.js';  // #85: shared reader
const GATEWAY_WS_URL = process.env.WATCH_GATEWAY_WS_URL || 'ws://127.0.0.1:8890';  // #84: match [gateway] default
export const VOICE_LIVE_PATH = '/api/voice/live';

function getCookie(req: IncomingMessage, name: string): string | null {
  const c = req.headers.cookie || '';
  const m = c.match(new RegExp('(?:^|;)\\s*' + name + '=([^;]+)'));
  return m ? decodeURIComponent(m[1]) : null;
}

// Same HMAC scheme as combo-proxy.js's makeSessionToken/verifySessionToken —
// the `orchestra_session` cookie that already gates every page, and every
// other WS (/ws/terminal), in front of this app. Duplicated here (not
// imported) per the file-ownership rule: combo-proxy.js is not edited and
// exports nothing importable.
function verifyOrchestraSession(token: string | null): boolean {
  if (!token) return false;
  const dot = token.indexOf('.');
  if (dot < 0) return false;
  const b64 = token.slice(0, dot);
  const sig = token.slice(dot + 1);
  const expected = createHmac('sha256', SESSION_SECRET).update(b64).digest('base64url');
  if (sig !== expected) return false;
  try {
    const payload = JSON.parse(Buffer.from(b64, 'base64url').toString());
    return typeof payload.exp === 'number' && payload.exp >= Date.now();
  } catch {
    return false;
  }
}

// Same JWT scheme as routes/token-auth.ts's verifyJwt (Bearer header or the
// `orchestra_token` cookie) — accepted as an alternate valid session (the
// multi-tenant login path uses this one instead of the VPS cookie).
function verifyOrchestraToken(req: IncomingMessage): boolean {
  let token: string | null = null;
  const auth = req.headers.authorization;
  if (auth?.startsWith('Bearer ')) token = auth.slice(7);
  if (!token) token = getCookie(req, 'orchestra_token');
  if (!token) return false;
  const parts = token.split('.');
  if (parts.length !== 3) return false;
  const [header, body, sig] = parts;
  const expected = createHmac('sha256', JWT_SECRET).update(`${header}.${body}`).digest('base64url');
  if (sig !== expected) return false;
  try {
    const payload = JSON.parse(Buffer.from(body, 'base64url').toString());
    return !payload.exp || payload.exp >= Math.floor(Date.now() / 1000);
  } catch {
    return false;
  }
}

export function browserIsAuthenticated(req: IncomingMessage): boolean {
  return verifyOrchestraSession(getCookie(req, 'orchestra_session')) || verifyOrchestraToken(req);
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

export function setupVoiceLiveWebSocket(httpServer: HttpServer, gatewayWsUrl = GATEWAY_WS_URL) {
  const wss = new WebSocketServer({ noServer: true });

  httpServer.prependListener('upgrade', (req: IncomingMessage, socket: Socket, head: Buffer) => {
    const url = new URL(req.url || '/', 'http://internal');
    if (url.pathname !== VOICE_LIVE_PATH) return; // not ours — fall through untouched (e.g. /ws/terminal)

    if (!isAllowedOrigin(req.headers.origin, req.headers.host)) {
      // CSWSH guard runs BEFORE auth — no auth oracle for cross-site callers.
      socket.write('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
      socket.destroy();
      return;
    }

    if (!browserIsAuthenticated(req)) {
      socket.write('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n');
      socket.destroy();
      return;
    }

    const gatewayToken = readGatewayToken();
    if (!gatewayToken) {
      // W1: LOUD failure at the HTTP-upgrade layer (before any WS handshake)
      // rather than a hung/silent connection.
      socket.write(
        'HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n'
        + `voice bridge unavailable: watch-gateway bearer token missing at ${gatewayTokenFile()}`,
      );
      socket.destroy();
      return;
    }

    const voice = url.searchParams.get('voice') || 'Fenrir';

    wss.handleUpgrade(req, socket, head, (clientWs) => {
      // MEDIUM finding (security review, commit 4840bf6247): the gateway
      // bearer must never appear in a URL (query strings land in access
      // logs, proxy logs, etc.). Send it as an Authorization header instead
      // — the same pattern routes/voice.ts already uses for /voice-call.
      const upstream = new WebSocket(
        `${gatewayWsUrl}/live?voice=${encodeURIComponent(voice)}`,
        { headers: { Authorization: `Bearer ${gatewayToken}` } },
      );

      let upstreamOpen = false;
      const pending: Array<{ data: any; isBinary: boolean }> = [];

      upstream.on('open', () => {
        upstreamOpen = true;
        for (const m of pending.splice(0)) upstream.send(m.data, { binary: m.isBinary });
      });

      upstream.on('unexpected-response', (_req, res) => {
        closeLoud(clientWs, 1011, `voice bridge upstream rejected the connection (HTTP ${res.statusCode})`);
      });

      upstream.on('message', (data, isBinary) => {
        if (clientWs.readyState === WebSocket.OPEN) clientWs.send(data as any, { binary: isBinary });
      });

      upstream.on('close', (code, reasonBuf) => {
        const reason = reasonBuf?.toString() || 'gateway closed the voice session';
        if (clientWs.readyState === WebSocket.OPEN) closeLoud(clientWs, code || 1011, reason);
      });

      upstream.on('error', (err) => {
        if (clientWs.readyState === WebSocket.OPEN) {
          closeLoud(clientWs, 1011, `voice bridge upstream error: ${err.message}`);
        }
      });

      clientWs.on('message', (data, isBinary) => {
        if (upstreamOpen && upstream.readyState === WebSocket.OPEN) {
          upstream.send(data as any, { binary: isBinary });
        } else {
          pending.push({ data, isBinary });
        }
      });

      clientWs.on('close', () => { try { upstream.close(); } catch { /* noop */ } });
      clientWs.on('error', () => { try { upstream.close(); } catch { /* noop */ } });
    });
  });
}
