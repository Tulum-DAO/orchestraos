/**
 * The live call carries the agent it is with: /api/voice/live?agent=<id> reaches the gateway's /live as `agent=<id>`,
 * once the id is a safe one the caller may see (the /api/agents/:id scope rule). No agent: today's call, unchanged.
 * A fake gateway stands in for watch_gateway.py; no real gateway, mic or vendor is touched.
 *
 * Run: npx tsx --test src/routes/voice-live.agent.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'http';
import { createHmac } from 'crypto';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { WebSocket, WebSocketServer } from 'ws';

const SESSION_SECRET = 'test-only-session-secret-not-a-real-key';  // pragma: allowlist secret
const BEARER = 'sample-gateway-bearer-5555';
process.env.SESSION_SECRET = SESSION_SECRET;
const dir = testTmpDir('voice-live-agent-');
mkdirSync(join(dir, 'state'));
writeFileSync(join(dir, 'registry.json'), JSON.stringify({ agents: { 'dev-demo': {}, 'pm-demo': {} } }));
writeFileSync(join(dir, 'state', 'watch-gateway-token'), `${BEARER}\n`);
process.env.ORCHESTRA_DIR = dir;
process.env.WATCH_GATEWAY_TOKEN_FILE = join(dir, 'state', 'watch-gateway-token');

const { setupVoiceLiveWebSocket, VOICE_LIVE_PATH } = await import('./voice-live.js');

function sessionCookie(): string {
  const b64 = Buffer.from(JSON.stringify({ u: 'operator', r: 'admin', c: '', exp: Date.now() + 3600_000 })).toString('base64url');
  return `orchestra_session=${b64}.${createHmac('sha256', SESSION_SECRET).update(b64).digest('base64url')}`;
}

/** Open one call; resolve with what the gateway saw (its /live URL) or the status and body the proxy refused with. */
async function call(query: string, headers: Record<string, string> = {},
                    end: (ws: WebSocket) => void = (ws) => ws.close(1000, 'done')):
    Promise<{ upstream?: URL; status?: number; body?: string; code?: number; reason?: string; ms?: number }> {
  const gw = new WebSocketServer({ port: 0, path: '/live' });
  await new Promise<void>((r) => gw.once('listening', () => r()));
  let upstream: URL | undefined;
  let endedAt = 0;
  gw.on('connection', (ws, req) => { upstream = new URL(req.url || '/', 'http://internal'); endedAt = Date.now(); end(ws); });
  const server = createServer((_q, s) => { s.writeHead(404); s.end(); });
  setupVoiceLiveWebSocket(server, `ws://127.0.0.1:${(gw.address() as any).port}`);
  await new Promise<void>((r) => server.listen(0, r));
  const port = (server.address() as any).port;
  try {
    return await new Promise((resolve, reject) => {
      const ws = new WebSocket(`ws://127.0.0.1:${port}${VOICE_LIVE_PATH}${query}`, {
        headers: { cookie: sessionCookie(), origin: `http://127.0.0.1:${port}`, ...headers },
      });
      ws.on('unexpected-response', (_r, res) => {
        let body = '';
        res.on('data', (c) => { body += c; });
        res.on('end', () => resolve({ status: res.statusCode, body }));
      });
      // A page left open is the failure being tested for: give up after 3 s and say so, never hang the run.
      const timer = setTimeout(() => { ws.removeAllListeners('close'); ws.terminate(); resolve({ upstream, code: -1, reason: 'still open' }); }, 3000);
      ws.on('close', (code, reason) => { clearTimeout(timer); resolve({ upstream, code, reason: reason.toString(), ms: Date.now() - endedAt }); });
      ws.on('error', (e) => { if (!upstream) reject(e); });
    });
  } finally {
    server.close();
    gw.close();
  }
}

const noBearer = (r: { upstream?: URL; body?: string }) => {
  assert.ok(!String(r.upstream ?? '').includes(BEARER), 'the bearer never rides in the URL');
  assert.ok(!(r.body ?? '').includes(BEARER), 'nor in a refusal');
};

test('the agent is forwarded to the gateway as agent=<id>', async () => {
  const r = await call('?voice=Charon&agent=dev-demo');
  assert.equal(r.upstream?.pathname, '/live');
  assert.equal(r.upstream?.searchParams.get('agent'), 'dev-demo');
  assert.equal(r.upstream?.searchParams.get('voice'), 'Charon');
  noBearer(r);
});

test('no agent: the gateway URL is exactly today\'s', async () => {
  const r = await call('?voice=Charon');
  assert.equal(r.upstream?.search, '?voice=Charon');
  const d = await call('');
  assert.equal(d.upstream?.search, '?voice=Fenrir');
  noBearer(r);
});

test('a bad agent id is refused before the gateway is dialled', async () => {
  for (const bad of ['..%2Fx', 'a%20b', '', '%3Bx']) {
    const r = await call(`?agent=${bad}`);
    assert.equal(r.status, 400, bad);
    assert.equal(r.upstream, undefined);
    noBearer(r);
  }
});

test('an agent the caller may not see is refused like an unknown one', async () => {
  const prev = process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
  process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = '1';
  try {
    const scoped = { 'x-orchestra-user': 'sample-user', 'x-orchestra-role': 'viewer', 'x-orchestra-allowed-agents': 'pm-demo' };
    const out = await call('?agent=dev-demo', scoped);
    assert.equal(out.status, 404);
    assert.equal(out.upstream, undefined);
    noBearer(out);
    const ok = await call('?agent=pm-demo', scoped);
    assert.equal(ok.upstream?.searchParams.get('agent'), 'pm-demo', 'an agent in scope goes through');
  } finally {
    if (prev === undefined) delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
    else process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = prev;
  }
});

test('the gateway ends the call with no close code: the page is told "call ended" with 1000, promptly', async () => {
  for (const [how, end] of [
    ['a close with no code', (ws: WebSocket) => ws.close()],
    ['a dropped connection', (ws: WebSocket) => ws.terminate()],
  ] as const) {
    const r = await call('?agent=dev-demo', {}, end);
    assert.equal(r.code, 1000, how);
    assert.equal(r.reason, 'call ended', how);
    assert.ok((r.ms ?? Infinity) < 2000, `${how}: closed in ${r.ms} ms`);
  }
});

test('a real close code from the gateway passes through unchanged', async () => {
  const r = await call('', {}, (ws) => ws.close(4001, 'sample reason'));
  assert.equal(r.code, 4001);
  assert.equal(r.reason, 'sample reason');
  const n = await call('', {}, (ws) => ws.close(1000, 'done'));
  assert.deepEqual([n.code, n.reason], [1000, 'done']);
});
