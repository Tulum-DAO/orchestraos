/**
 * Every voice WebSocket outcome leaves exactly one log line, on both bridges (/api/voice/live and /api/voice/native):
 * refused-origin, refused-auth (which of cookie / token / tailscale failed and how), refused-request, refused-no-bearer,
 * aborted-handshake, dialed, upstream-refused, closed. The line names the path, the user when known and the requester
 * address, and never a credential. A stand-in gateway on loopback plays watch_gateway.py; nothing real is touched.
 * Run: npx tsx --test src/routes/voice-ws-log.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import http, { createServer } from 'node:http';
import { createHmac } from 'node:crypto';
import { writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { WebSocket, WebSocketServer } from 'ws';

const SESSION_SECRET = 'test-only-session-secret-not-a-real-key';  // pragma: allowlist secret
const JWT_SECRET = 'test-only-jwt-secret-not-a-real-key';  // pragma: allowlist secret
process.env.SESSION_SECRET = SESSION_SECRET;
process.env.JWT_SECRET = JWT_SECRET;
delete process.env.ORCHESTRA_TAILSCALE_USERS;
// A recognisable fake bearer: the one string that must never reach a log line.
const BEARER = 'planted-fake-gateway-bearer-ZZ987654321';
const dir = testTmpDir('voice-ws-log-');
const tokenFile = join(dir, 'watch-gateway-token');
writeFileSync(tokenFile, BEARER + '\n');
process.env.WATCH_GATEWAY_TOKEN_FILE = tokenFile;

// The stand-in gateway: /live and /arturo/gptlive/ws, or a pre-upgrade refusal when `refuseWith` is set.
let refuseWith = 0;
let seenAuth: string | undefined;
const gwWss = new WebSocketServer({ noServer: true });
const gateway = http.createServer((_req, res) => { res.writeHead(404); res.end(); });
gateway.on('upgrade', (req, socket, head) => {
  seenAuth = req.headers.authorization;
  if (refuseWith) {
    socket.write(`HTTP/1.1 ${refuseWith} Refused\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
    socket.destroy();
    return;
  }
  gwWss.handleUpgrade(req, socket, head, (ws) => setTimeout(() => ws.close(1000, 'idle'), 30));
});
await new Promise<void>((r) => gateway.listen(0, '127.0.0.1', () => r()));
const gwPort = (gateway.address() as AddressInfo).port;
process.env.WATCH_GATEWAY_URL = `http://127.0.0.1:${gwPort}`;

const { setupVoiceLiveWebSocket, VOICE_LIVE_PATH } = await import('./voice-live.js');
const { setupVoiceNativeWebSocket, VOICE_NATIVE_PATH } = await import('./voice-native.js');

const proxy = createServer((_req, res) => { res.writeHead(404); res.end(); });
setupVoiceLiveWebSocket(proxy, `ws://127.0.0.1:${gwPort}`);
setupVoiceNativeWebSocket(proxy, `ws://127.0.0.1:${gwPort}`);
await new Promise<void>((r) => proxy.listen(0, '127.0.0.1', () => r()));
const port = (proxy.address() as AddressInfo).port;
after(() => { proxy.close(); gateway.close(); gwWss.close(); });

function sessionCookie(over: Record<string, unknown> = {}, secret = SESSION_SECRET): string {
  const b64 = Buffer.from(JSON.stringify({ u: 'sample-operator', r: 'admin', c: '', exp: Date.now() + 3600_000, ...over })).toString('base64url');
  return `orchestra_session=${b64}.${createHmac('sha256', secret).update(b64).digest('base64url')}`;
}
function jwt(over: Record<string, unknown> = {}, secret = JWT_SECRET): string {
  const header = Buffer.from(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).toString('base64url');
  const body = Buffer.from(JSON.stringify({ sub: 'sample-tenant', role: 'admin', exp: Math.floor(Date.now() / 1000) + 3600, ...over })).toString('base64url');
  return `${header}.${body}.${createHmac('sha256', secret).update(`${header}.${body}`).digest('base64url')}`;
}
const sameOrigin = () => `http://127.0.0.1:${port}`;

/** Run `fn` with console.log captured; resolve with the [voice-ws ...] lines it wrote. */
async function captured(fn: () => Promise<void>): Promise<string[]> {
  const lines: string[] = [];
  const orig = console.log;
  console.log = (...a: unknown[]) => { lines.push(a.map(String).join(' ')); };
  try { await fn(); await new Promise((r) => setTimeout(r, 60)); }   // the close line lands after the socket closes
  finally { console.log = orig; }
  return lines.filter((l) => l.startsWith('[voice-ws '));
}
const withOutcome = (lines: string[], outcome: string) => lines.filter((l) => l.includes(` outcome=${outcome} `) || l.endsWith(` outcome=${outcome}`));
function exactlyOne(lines: string[], outcome: string): string {
  const got = withOutcome(lines, outcome);
  assert.equal(got.length, 1, `expected exactly one outcome=${outcome} line, got ${got.length}:\n${lines.join('\n')}`);
  return got[0];
}
function neverACredential(lines: string[], ...extra: string[]) {
  for (const l of lines) {
    for (const s of [BEARER, SESSION_SECRET, JWT_SECRET, ...extra]) assert.ok(!l.includes(s), `a credential in a log line: ${l}`);
  }
}

/** Open a browser socket; resolve once it is refused (HTTP status) or closed (code, reason). */
function connect(path: string, headers: Record<string, string>): Promise<{ status?: number; code?: number; reason?: string }> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://127.0.0.1:${port}${path}`, { headers });
    ws.on('unexpected-response', (_r, res) => { res.resume(); res.on('end', () => resolve({ status: res.statusCode })); });
    ws.on('close', (code, reason) => resolve({ code, reason: reason.toString() }));
    ws.on('error', (e) => { if (ws.readyState === WebSocket.CONNECTING) reject(e); });
    setTimeout(() => { ws.terminate(); resolve({ code: -1, reason: 'test timeout' }); }, 3000).unref();
  });
}

const routes: Array<[string, string, string]> = [
  ['live', VOICE_LIVE_PATH, '/live?voice=Fenrir'],
  ['native', VOICE_NATIVE_PATH, '/arturo/gptlive/ws'],
];

for (const [name, path, upstreamPath] of routes) {
  test(`${name}: refused-origin is one line naming origin and host`, async () => {
    const lines = await captured(async () => {
      const r = await connect(path, { cookie: sessionCookie(), origin: 'https://evil.example' });
      assert.equal(r.status, 403);
    });
    assert.equal(lines.length, 1);
    const l = exactlyOne(lines, 'refused-origin');
    assert.match(l, /^\[voice-ws \d{4}-\d{2}-\d{2}T[^\]]+Z\] /);
    assert.ok(l.includes(`path=${path} `));
    assert.ok(l.includes('origin=https://evil.example'));
    assert.ok(l.includes(`host=127.0.0.1:${port}`));
    assert.ok(l.includes('addr=127.0.0.1'));
    assert.ok(l.includes('status=403'));
    neverACredential(lines);
  });

  test(`${name}: a missing Origin is refused-origin with origin=absent`, async () => {
    const lines = await captured(async () => {
      const r = await connect(path, { cookie: sessionCookie() });
      assert.equal(r.status, 403);
    });
    assert.ok(exactlyOne(lines, 'refused-origin').includes('origin=absent'));
  });

  test(`${name}: refused-auth says which of cookie / token / tailscale failed and how, never the value`, async () => {
    // Every failing credential below carries an attacker-chosen name: a refusal must never present it as user=.
    const PLANTED = 'planted-attacker-name';
    const cookie = sessionCookie({ u: PLANTED }, 'not-the-secret');
    const none = await captured(async () => assert.equal((await connect(path, { origin: sameOrigin() })).status, 401));
    assert.equal(none.length, 1);
    const l0 = exactlyOne(none, 'refused-auth');
    assert.ok(l0.includes('reason=cookie:absent,token:absent,tailscale:absent'), l0);
    assert.ok(l0.includes('status=401'));
    assert.ok(!l0.includes('user='), 'no user is known on a refusal with no identity');

    const bad = await captured(async () => assert.equal((await connect(path, { origin: sameOrigin(), cookie })).status, 401));
    assert.ok(exactlyOne(bad, 'refused-auth').includes('cookie:invalid,token:absent,tailscale:absent'), bad[0]);
    neverACredential(bad, cookie.slice('orchestra_session='.length), PLANTED, 'user=');

    const expired = await captured(async () => assert.equal((await connect(path, { origin: sameOrigin(), cookie: sessionCookie({ u: PLANTED, exp: Date.now() - 1000 }) })).status, 401));
    assert.ok(exactlyOne(expired, 'refused-auth').includes('cookie:expired,'), expired[0]);
    neverACredential(expired, PLANTED, 'user=');

    const badJwt = jwt({ sub: PLANTED }, 'not-the-secret');
    const tok = await captured(async () => assert.equal((await connect(path, { origin: sameOrigin(), authorization: `Bearer ${badJwt}` })).status, 401));
    assert.ok(exactlyOne(tok, 'refused-auth').includes('cookie:absent,token:invalid,tailscale:absent'), tok[0]);
    neverACredential(tok, badJwt, PLANTED, 'user=');

    const oldJwt = jwt({ sub: PLANTED, exp: Math.floor(Date.now() / 1000) - 10 });
    const old = await captured(async () => assert.equal((await connect(path, { origin: sameOrigin(), cookie: `orchestra_token=${oldJwt}` })).status, 401));
    assert.ok(exactlyOne(old, 'refused-auth').includes('token:expired,'), old[0]);
    neverACredential(old, oldJwt, PLANTED, 'user=');

    // A Tailscale login that is not on the allowlist (none is configured here) is a claim, not an identity: not named.
    const ts = await captured(async () => assert.equal((await connect(path, { origin: sameOrigin(), 'tailscale-user-login': `${PLANTED}@example.test` })).status, 401));
    const lt = exactlyOne(ts, 'refused-auth');
    assert.ok(lt.includes('tailscale:not-allowed'), lt);
    neverACredential(ts, PLANTED, 'user=');
  });

  test(`${name}: refused-no-bearer when the gateway token file is missing`, async () => {
    process.env.WATCH_GATEWAY_TOKEN_FILE = join(dir, 'missing-token');
    try {
      const lines = await captured(async () => assert.equal((await connect(path, { origin: sameOrigin(), cookie: sessionCookie() })).status, 502));
      assert.equal(lines.length, 1);
      const l = exactlyOne(lines, 'refused-no-bearer');
      assert.ok(l.includes('user=sample-operator'));
      assert.ok(l.includes('status=502'));
    } finally { process.env.WATCH_GATEWAY_TOKEN_FILE = tokenFile; }
  });

  test(`${name}: a malformed upgrade (no Sec-WebSocket-Key) is aborted-handshake, one line`, async () => {
    const lines = await captured(() => new Promise<void>((resolve, reject) => {
      const req = http.request({ host: '127.0.0.1', port, path, headers: { connection: 'Upgrade', upgrade: 'websocket', origin: sameOrigin(), cookie: sessionCookie() } });
      req.on('response', (res) => { assert.equal(res.statusCode, 400); res.resume(); res.on('end', resolve); });
      req.on('upgrade', () => reject(new Error('must not upgrade without a key')));
      req.on('error', reject);
      req.end();
    }));
    assert.equal(lines.length, 1);
    assert.ok(exactlyOne(lines, 'aborted-handshake').includes('user=sample-operator'));
  });

  test(`${name}: a call is dialed (upstream path, no credentials) then closed (code, reason, duration)`, async () => {
    seenAuth = undefined;
    const lines = await captured(async () => {
      const r = await connect(path, { origin: sameOrigin(), cookie: sessionCookie() });
      assert.deepEqual([r.code, r.reason], [1000, 'idle']);
    });
    assert.equal(seenAuth, `Bearer ${BEARER}`, 'the real bearer was sent to the gateway');
    assert.equal(lines.length, 2, lines.join('\n'));
    const d = exactlyOne(lines, 'dialed');
    assert.ok(d.includes(`upstream=ws://127.0.0.1:${gwPort}${upstreamPath}`), d);
    assert.ok(d.includes('user=sample-operator'), d);
    assert.ok(d.includes(`path=${path} `), d);
    const c = exactlyOne(lines, 'closed');
    assert.ok(c.includes('code=1000'), c);
    assert.ok(c.includes('reason=idle'), c);
    assert.match(c, / ms=\d+/);
    assert.ok(c.includes('user=sample-operator'), c);
    neverACredential(lines);
  });

  test(`${name}: a JWT session names the token's subject; a Tailscale operator names the login`, async () => {
    const lines = await captured(async () => { await connect(path, { origin: sameOrigin(), cookie: `orchestra_token=${jwt()}` }); });
    const j = exactlyOne(lines, 'dialed');
    assert.ok(j.includes('user=sample-tenant ') && j.includes('via=token'), j);
    process.env.ORCHESTRA_TAILSCALE_USERS = 'operator@example.test';
    try {
      const ts = await captured(async () => { await connect(path, { origin: sameOrigin(), 'tailscale-user-login': 'Operator@example.test' }); });
      const t = exactlyOne(ts, 'dialed');
      assert.ok(t.includes('user=operator@example.test ') && t.includes('via=tailscale'), t);
    } finally { delete process.env.ORCHESTRA_TAILSCALE_USERS; }
  });

  test(`${name}: upstream-refused carries the gateway's HTTP status, once, and the socket still gets its closed line`, async () => {
    refuseWith = 503;
    try {
      const lines = await captured(async () => {
        const r = await connect(path, { origin: sameOrigin(), cookie: sessionCookie() });
        assert.ok((r.code ?? 0) >= 1011, `closed with ${r.code}`);
      });
      assert.equal(lines.length, 3, lines.join('\n'));
      exactlyOne(lines, 'dialed');
      assert.ok(exactlyOne(lines, 'upstream-refused').includes('status=503'));
      exactlyOne(lines, 'closed');
      neverACredential(lines);
    } finally { refuseWith = 0; }
  });

  test(`${name}: an unreachable gateway is upstream-refused with the error code`, async () => {
    const srv = createServer((_q, s) => { s.writeHead(404); s.end(); });
    const dead = http.createServer(); await new Promise<void>((r) => dead.listen(0, '127.0.0.1', () => r()));
    const deadPort = (dead.address() as AddressInfo).port; await new Promise<void>((r) => dead.close(() => r()));
    (name === 'live' ? setupVoiceLiveWebSocket : setupVoiceNativeWebSocket)(srv, `ws://127.0.0.1:${deadPort}`);
    await new Promise<void>((r) => srv.listen(0, '127.0.0.1', () => r()));
    const p2 = (srv.address() as AddressInfo).port;
    try {
      const lines = await captured(() => new Promise<void>((resolve) => {
        const ws = new WebSocket(`ws://127.0.0.1:${p2}${path}`, { headers: { origin: `http://127.0.0.1:${p2}`, cookie: sessionCookie() } });
        ws.on('close', () => resolve());
      }));
      assert.ok(exactlyOne(lines, 'upstream-refused').includes('error=ECONNREFUSED'), lines.join('\n'));
      exactlyOne(lines, 'closed');
    } finally { srv.close(); }
  });
}

test('live: a bad agent id is refused-request with its status, before the gateway is dialed', async () => {
  seenAuth = undefined;
  const lines = await captured(async () => assert.equal((await connect(`${VOICE_LIVE_PATH}?agent=..%2Fx`, { origin: sameOrigin(), cookie: sessionCookie() })).status, 400));
  assert.equal(lines.length, 1);
  const l = exactlyOne(lines, 'refused-request');
  assert.ok(l.includes('status=400'), l);
  assert.ok(l.includes('reason="bad agent id"'), l);
  assert.equal(seenAuth, undefined);
});

test('a non-voice upgrade path leaves no voice-ws line (the bridge falls through)', async () => {
  const lines = await captured(async () => { await connect('/ws/other', { origin: sameOrigin(), cookie: sessionCookie() }).catch(() => ({})); });
  assert.deepEqual(lines, []);
});

after(() => rmSync(dir, { recursive: true, force: true }));
