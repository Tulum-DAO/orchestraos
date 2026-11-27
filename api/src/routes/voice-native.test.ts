/**
 * /api/voice/native (WS), /api/voice/native/ready and /api/voice/call-ended. A stand-in gateway on loopback plays
 * watch_gateway.py: nothing reaches a real gateway, arturo or a voice vendor.
 * Run: npx tsx --test src/routes/voice-native.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import http, { createServer } from 'node:http';
import { createHmac } from 'node:crypto';
import { writeFileSync, chmodSync } from 'node:fs';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import express from 'express';
import { WebSocket, WebSocketServer } from 'ws';

const SESSION_SECRET = 'test-only-session-secret-not-a-real-key';  // pragma: allowlist secret
process.env.SESSION_SECRET = SESSION_SECRET;
const BEARER = 'test-fleet-bearer-456';
const dir = testTmpDir('voice-native-');
const tokenFile = join(dir, 'watch-gateway-token');
writeFileSync(tokenFile, BEARER + '\n');
process.env.WATCH_GATEWAY_TOKEN_FILE = tokenFile;
delete process.env.ORCHESTRA_TAILSCALE_USERS;

// The stand-in gateway: HTTP for /voice-call-ended, and the native socket at /arturo/gptlive/ws (or a pre-upgrade
// status when `refuseWith` is set).
let refuseWith = 0;
let hangUpgrade = false;   // the gateway takes the upgrade and never answers it
let lastUpgrade: { url?: string; auth?: string } = {};
let lastPost: { auth?: string; body?: any } = {};
let postStatus = 200;
let onGatewaySocket: (ws: WebSocket) => void = () => {};
const gwWss = new WebSocketServer({ noServer: true });
const hung: any[] = [];
const gateway = http.createServer((req, res) => {
  if (req.method === 'POST' && req.url === '/voice-call-ended') {
    let raw = '';
    req.on('data', (c) => { raw += c; });
    req.on('end', () => {
      lastPost = { auth: req.headers.authorization, body: JSON.parse(raw) };
      res.writeHead(postStatus, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(postStatus === 200 ? { ok: true, call_id: 'vc_client_0123456789ab' } : { ok: false, error: 'nope' }));
    });
    return;
  }
  res.writeHead(404); res.end();
});
gateway.on('upgrade', (req, socket, head) => {
  lastUpgrade = { url: req.url, auth: req.headers.authorization };
  if (hangUpgrade) { hung.push(socket); return; }
  if (refuseWith) {
    socket.write(`HTTP/1.1 ${refuseWith} Refused\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
    socket.destroy();
    return;
  }
  if (req.url !== '/arturo/gptlive/ws') { socket.destroy(); return; }
  gwWss.handleUpgrade(req, socket, head, (ws) => onGatewaySocket(ws));
});
await new Promise<void>((r) => gateway.listen(0, '127.0.0.1', () => r()));
const gwPort = (gateway.address() as AddressInfo).port;
process.env.WATCH_GATEWAY_URL = `http://127.0.0.1:${gwPort}`;

const { bridgeBrowserSocket, UPSTREAM_HANDSHAKE_MS, MAX_PENDING_BYTES } = await import('./voice-live.js');
const { setupVoiceNativeWebSocket, VOICE_NATIVE_PATH, createVoiceNativeRouter, voiceNativeReady, fetchNativeHealth,
  validateCallEnded, nativeRejection, realCallEndedDeps, NATIVE_DOWN, NATIVE_OFF, NATIVE_SLOW, NATIVE_NOT_SET_UP,
  nativeHealthUrl, fitReason, ConvSeen } = await import('./voice-native.js');

const proxy = createServer((_req, res) => { res.writeHead(404); res.end(); });
setupVoiceNativeWebSocket(proxy, `ws://127.0.0.1:${gwPort}`);
await new Promise<void>((r) => proxy.listen(0, '127.0.0.1', () => r()));
const port = (proxy.address() as AddressInfo).port;
after(() => { for (const h of hung) h.destroy(); proxy.close(); gateway.close(); gwWss.close(); });

function sessionCookie(): string {
  const payload = JSON.stringify({ u: 'operator', r: 'admin', c: '', exp: Date.now() + 3600_000 });
  const b64 = Buffer.from(payload).toString('base64url');
  const sig = createHmac('sha256', SESSION_SECRET).update(b64).digest('base64url');
  return `orchestra_session=${b64}.${sig}`;
}
const goodHeaders = () => ({ cookie: sessionCookie(), origin: `http://127.0.0.1:${port}` });

const opened: WebSocket[] = [];
after(() => { for (const w of opened) w.terminate(); });   // a socket a refusal test did not expect never pins the run

/** Open the browser socket; resolves with the HTTP status of a refused upgrade, or the open socket. */
function open(headers: Record<string, string>): Promise<{ status: number } | { ws: WebSocket }> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://127.0.0.1:${port}${VOICE_NATIVE_PATH}`, { headers });
    ws.on('unexpected-response', (_r, res) => resolve({ status: res.statusCode || 0 }));
    ws.on('open', () => { opened.push(ws); resolve({ ws }); });
    ws.on('error', reject);
  });
}

function closed(ws: WebSocket): Promise<{ code: number; reason: string }> {
  return new Promise((r) => ws.on('close', (code, reason) => r({ code, reason: reason.toString() })));
}

// ── the socket ─────────────────────────────────────────────────────────────────────────────────────────────────────

test('refuses a cross-site Origin with 403 before auth, and never dials the gateway', { timeout: 8000 }, async () => {
  lastUpgrade = {};
  const r = await open({ cookie: sessionCookie(), origin: 'https://evil.example' });
  assert.deepEqual(r, { status: 403 });
  assert.deepEqual(lastUpgrade, {});
});

test('refuses no session with 401 (same-host Origin), and never dials the gateway', { timeout: 8000 }, async () => {
  lastUpgrade = {};
  const r = await open({ origin: `http://127.0.0.1:${port}` });
  assert.deepEqual(r, { status: 401 });
  assert.deepEqual(lastUpgrade, {});
});

test('dials /arturo/gptlive/ws with the fleet bearer (header, no query), and frames pass verbatim both ways', { timeout: 8000 }, async () => {
  const gotUp: Array<{ isBinary: boolean; data: Buffer }> = [];
  onGatewaySocket = (gw) => {
    gw.on('message', (data, isBinary) => {
      gotUp.push({ isBinary, data: data as Buffer });
      if (gotUp.length === 2) {
        gw.send(JSON.stringify({ type: 'state', mode: 'live', conversation_id: 'native-0123456789ab' }));
        gw.send(Buffer.from([7, 0, 8, 0]), { binary: true });
      }
    });
  };
  const r = await open({ ...goodHeaders() });
  assert.ok('ws' in r);
  const ws = r.ws;
  // Sent right after open, before the upstream is necessarily up: buffered, then delivered in order.
  ws.send(JSON.stringify({ type: 'start', settle: 'fast' }));
  ws.send(Buffer.alloc(3200, 1), { binary: true });
  const down: Array<{ isBinary: boolean; data: Buffer }> = [];
  await new Promise<void>((resolve) => ws.on('message', (data, isBinary) => {
    down.push({ isBinary, data: data as Buffer });
    if (down.length === 2) resolve();
  }));
  ws.close();
  assert.equal(lastUpgrade.url, '/arturo/gptlive/ws');
  assert.equal(lastUpgrade.auth, `Bearer ${BEARER}`);
  assert.equal(gotUp[0].isBinary, false);
  assert.deepEqual(JSON.parse(gotUp[0].data.toString()), { type: 'start', settle: 'fast' });
  assert.equal(gotUp[1].isBinary, true);
  assert.equal(gotUp[1].data.length, 3200);
  assert.equal(down[0].isBinary, false);
  assert.equal(JSON.parse(down[0].data.toString()).type, 'state');
  assert.equal(down[1].isBinary, true);
  assert.deepEqual([...down[1].data], [7, 0, 8, 0]);
});

for (const [status, code, words] of [[409, 4409, 'busy: a call is already open'], [503, 4503, 'switched off'],
  [429, 4429, 'too many'], [502, 4502, 'not answering'], [401, 4401, 'did not accept']] as const) {
  test(`a gateway ${status} before the upgrade reaches the page as close ${code} with a readable reason`, { timeout: 8000 }, async () => {
    refuseWith = status;
    try {
      const r = await open(goodHeaders());
      assert.ok('ws' in r);
      const c = await closed(r.ws);
      assert.equal(c.code, code);
      assert.match(c.reason, new RegExp(words));
    } finally { refuseWith = 0; }
  });
}

test('the gateway\'s own close codes pass through (1000 idle, 1001 restart, 1011 dropped)', { timeout: 8000 }, async () => {
  for (const [code, reason] of [[1000, 'idle'], [1001, 'gateway restart'], [1011, 'arturo dropped']] as const) {
    onGatewaySocket = (gw) => setTimeout(() => gw.close(code, reason), 20);
    const r = await open(goodHeaders());
    assert.ok('ws' in r);
    assert.deepEqual(await closed(r.ws), { code, reason });
  }
});

/** Rejects with `what` once `ms` pass, so a broken limit fails here by name instead of as a cancelled timeout. */
function within<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  let t: NodeJS.Timeout;
  return Promise.race([p, new Promise<T>((_r, rej) => { t = setTimeout(() => rej(new Error(`${what} (none within ${ms} ms)`)), ms); })])
    .finally(() => clearTimeout(t));
}

test('the shipped limits: 10 s for the upstream handshake, 2 MB of frames held for it', () => {
  assert.equal(UPSTREAM_HANDSHAKE_MS, 10_000);
  assert.equal(MAX_PENDING_BYTES, 2 * 1024 * 1024);
});

test('a stalled upstream: under 2 MB held the call stays up, past 2 MB the browser is closed 1009', { timeout: 8000 }, async () => {
  hangUpgrade = true;
  try {
    const r = await open(goodHeaders());
    assert.ok('ws' in r);
    const c = closed(r.ws);
    let early: { code: number } | null = null;
    c.then((v) => { early = v; });
    const frame = 3200;
    const under = Math.floor((MAX_PENDING_BYTES * 0.9) / frame);              // ~1.8 MB: the cap must not bite yet
    for (let i = 0; i < under; i++) r.ws.send(Buffer.alloc(frame), { binary: true });
    await new Promise((res) => setTimeout(res, 500));
    assert.equal(early, null, 'closed while still under MAX_PENDING_BYTES');
    assert.equal(r.ws.readyState, WebSocket.OPEN);
    for (let i = 0; i < Math.ceil((MAX_PENDING_BYTES * 0.2) / frame); i++) r.ws.send(Buffer.alloc(frame), { binary: true });
    const got = await within(c, 3000, 'no close after passing MAX_PENDING_BYTES');
    assert.equal(got.code, 1009);
  } finally { hangUpgrade = false; }
});

test('a stalled upstream handshake times out on the bridge\'s own timer and the browser is told', { timeout: 8000 }, async () => {
  hangUpgrade = true;
  const srv = createServer((_q, s2) => { s2.writeHead(404); s2.end(); });
  bridgeBrowserSocket(srv, `ws://127.0.0.1:${gwPort}`, {
    path: '/t/handshake', upstream: () => ({ path: '/arturo/gptlive/ws' }), handshakeMs: 200,
    onUpstreamRejected: () => {}, onUpstreamClosed: () => {},
    onUpstreamError: (ws, err) => ws.close(1011, err.message.slice(0, 100)),
  });
  await new Promise<void>((r) => srv.listen(0, '127.0.0.1', () => r()));
  const p2 = (srv.address() as AddressInfo).port;
  try {
    const t0 = Date.now();
    const ws = new WebSocket(`ws://127.0.0.1:${p2}/t/handshake`, { headers: { cookie: sessionCookie(), origin: `http://127.0.0.1:${p2}` } });
    const got = await within(closed(ws), 2000, 'the browser was not told the upstream handshake timed out');
    assert.equal(got.code, 1011);
    assert.match(got.reason, /handshake has timed out/i);
    assert.ok(Date.now() - t0 >= 150, `closed after ${Date.now() - t0} ms: before the 200 ms handshake timer`);
  } finally { hangUpgrade = false; srv.close(); }
});

test('nativeRejection: any status maps into 4000-4999, unknowns say the status', { timeout: 8000 }, () => {
  assert.deepEqual(nativeRejection(418), { code: 4418, reason: 'refused: the voice gateway answered HTTP 418' });
  assert.equal(nativeRejection(0).code, 4502);
});

// ── GET /native/ready ──────────────────────────────────────────────────────────────────────────────────────────────

test('voiceNativeReady: ok / switched off / down / timeout / no bearer', { timeout: 8000 }, async () => {
  const d = (h: any, tok = true) => ({ hasGatewayToken: () => tok, health: async () => h });
  assert.deepEqual(await voiceNativeReady(d({ ok: true, enabled: true, calls: 0 })), { ok: true });
  assert.deepEqual(await voiceNativeReady(d({ ok: true, enabled: false })), { ok: false, reason: NATIVE_OFF });
  assert.deepEqual(await voiceNativeReady(d({ ok: false })), { ok: false, reason: NATIVE_DOWN });
  assert.deepEqual(await voiceNativeReady(d('down')), { ok: false, reason: NATIVE_DOWN });
  assert.deepEqual(await voiceNativeReady(d('timeout')), { ok: false, reason: NATIVE_SLOW });
  assert.deepEqual(await voiceNativeReady(d({ ok: true, enabled: true }, false)), { ok: false, reason: NATIVE_NOT_SET_UP });
});

test('fetchNativeHealth: a real answer, a refused port, and a server that never answers (timeout)', { timeout: 8000 }, async () => {
  let mode: 'ok' | 'hang' | '500' = 'ok';
  const native = http.createServer((req, res) => {
    if (mode === 'hang') return;   // never answers
    if (mode === '500') { res.writeHead(500); res.end(); return; }
    assert.equal(req.url, '/native/healthz');
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, enabled: true, calls: 0, claim_gate: true }));
  });
  await new Promise<void>((r) => native.listen(0, '127.0.0.1', () => r()));
  const p = (native.address() as AddressInfo).port;
  try {
    assert.equal(nativeHealthUrl({ ORCHESTRA_ARTURO_NATIVE_PORT: String(p) }), `http://127.0.0.1:${p}/native/healthz`);
    assert.equal(nativeHealthUrl({}), 'http://127.0.0.1:5076/native/healthz');
    const url = nativeHealthUrl({ ORCHESTRA_ARTURO_NATIVE_PORT: String(p) });
    assert.deepEqual(await fetchNativeHealth(url), { ok: true, enabled: true, calls: 0, claim_gate: true });
    mode = '500';
    assert.equal(await fetchNativeHealth(url), 'down');
    mode = 'hang';
    const t0 = Date.now();
    assert.equal(await fetchNativeHealth(url, 150), 'timeout');
    assert.ok(Date.now() - t0 < 2000);
  } finally { native.closeAllConnections(); native.close(); }
  assert.equal(await fetchNativeHealth(`http://127.0.0.1:${p}/native/healthz`), 'down');   // closed port
});

// ── the HTTP routes, mounted ───────────────────────────────────────────────────────────────────────────────────────

let healthAnswer: any = { ok: true, enabled: true };
const app = express();
app.use(express.json({ limit: '10mb' }));
app.use('/api/voice', createVoiceNativeRouter({ hasGatewayToken: () => true, health: async () => healthAnswer }));
const appServer = app.listen(0, '127.0.0.1');
await new Promise<void>((r) => appServer.once('listening', () => r()));
const base = `http://127.0.0.1:${(appServer.address() as AddressInfo).port}`;
after(() => appServer.close());

// combo-proxy's shared secret: a 0600 fixture, the same file the API is told to read.
const PROXY_SECRET = 'test-only-proxy-secret-0123456789abcdef';  // pragma: allowlist secret
const proxyFile = join(dir, 'orchestra-proxy-auth.secret');
writeFileSync(proxyFile, PROXY_SECRET + '\n', { mode: 0o600 });
chmodSync(proxyFile, 0o600);
process.env.ORCHESTRA_PROXY_AUTH_FILE = proxyFile;
const comboHeaders = (over: Record<string, string> = {}) =>
  ({ 'x-orchestra-proxy-auth': PROXY_SECRET, 'x-orchestra-user': 'operator', 'x-orchestra-role': 'admin', ...over });

test('GET /native/ready: refused without a session, answers with one', { timeout: 8000 }, async () => {
  const no = await fetch(`${base}/api/voice/native/ready`);
  assert.equal(no.status, 401);
  healthAnswer = { ok: true, enabled: true };
  const yes = await fetch(`${base}/api/voice/native/ready`, { headers: { cookie: sessionCookie() } });
  assert.deepEqual(await yes.json(), { ok: true });
});

test('combo vouching: a bare x-orchestra-user is REFUSED; the proxy secret + admin role is accepted', { timeout: 8000 }, async () => {
  const bare = await fetch(`${base}/api/voice/native/ready`, { headers: { 'x-orchestra-user': 'operator', 'x-orchestra-role': 'admin' } });
  assert.equal(bare.status, 401);
  healthAnswer = 'down';
  const combo = await fetch(`${base}/api/voice/native/ready`, { headers: comboHeaders() });
  assert.equal(combo.status, 200);
  assert.deepEqual(await combo.json(), { ok: false, reason: NATIVE_DOWN });
});

test('combo vouching: a wrong secret is refused', { timeout: 8000 }, async () => {
  for (const wrong of ['test-only-proxy-secret-0123456789abcdeX', 'short', PROXY_SECRET + 'x']) {
    const r = await fetch(`${base}/api/voice/native/ready`, { headers: comboHeaders({ 'x-orchestra-proxy-auth': wrong }) });
    assert.equal(r.status, 401, wrong);
  }
});

test('combo vouching: a vouched non-admin is refused, and so is a vouched request with no user', { timeout: 8000 }, async () => {
  const member = await fetch(`${base}/api/voice/native/ready`, { headers: comboHeaders({ 'x-orchestra-role': 'member' }) });
  assert.equal(member.status, 401);
  const h = comboHeaders(); delete (h as Record<string, string>)['x-orchestra-user'];
  const nouser = await fetch(`${base}/api/voice/call-ended`, { method: 'POST', headers: { ...h, 'Content-Type': 'application/json' }, body: '{}' });
  assert.equal(nouser.status, 401);
});

test('combo vouching: a secret file others can read is ignored', { timeout: 8000 }, async () => {
  chmodSync(proxyFile, 0o644);
  try {
    const r = await fetch(`${base}/api/voice/native/ready`, { headers: comboHeaders() });
    assert.equal(r.status, 401);
  } finally { chmodSync(proxyFile, 0o600); }
});

const NOW = Math.floor(Date.now() / 1000);
const goodBody = (conv = 'native-0123456789ab') => ({ started_at: NOW - 60, ended_at: NOW, conv_id: conv,
  turns: [{ role: 'user', text: 'hi', ts: NOW - 59 }, { role: 'arturo', text: 'hello', ts: NOW - 58 }] });

test('validateCallEnded: accepts the contract shape, refuses bad shapes and oversize input', { timeout: 8000 }, () => {
  const ok = validateCallEnded(goodBody());
  assert.ok(ok.ok);
  const v = (b: any) => validateCallEnded(b) as { ok: false; status: number; error: string };
  assert.equal(v(null).status, 400);
  assert.equal(v({ ...goodBody(), started_at: 0 }).status, 400);
  assert.equal(v({ ...goodBody(), ended_at: NOW - 61 }).status, 400);          // before started_at
  assert.equal(v({ ...goodBody(), conv_id: 'vc_live_x' }).status, 400);
  assert.equal(v({ ...goodBody(), turns: 'x' }).status, 400);
  assert.equal(v({ ...goodBody(), turns: [{ role: 'agent', text: 'x' }] }).status, 400);
  assert.equal(v({ ...goodBody(), turns: [{ role: 'user', text: 5 }] }).status, 400);
  assert.equal(v({ ...goodBody(), turns: [{ role: 'user', text: 'x', ts: 'now' }] }).status, 400);
  assert.equal(v({ ...goodBody(), pad: 'x'.repeat(1024 * 1024) }).status, 413);
});

test('validateCallEnded: ended_at within 5 min of now, and a call at most 4 h long', { timeout: 8000 }, () => {
  const v = (b: any) => validateCallEnded(b, NOW);
  assert.ok(v({ ...goodBody(), ended_at: NOW + 299 }).ok);
  assert.ok(v({ ...goodBody(), started_at: NOW - 400, ended_at: NOW - 299 }).ok);
  assert.equal((v({ ...goodBody(), ended_at: NOW + 301 }) as any).status, 400);
  assert.equal((v({ ...goodBody(), started_at: NOW - 400, ended_at: NOW - 301 }) as any).status, 400);
  assert.ok(v({ ...goodBody(), started_at: NOW - 4 * 3600 }).ok);
  assert.equal((v({ ...goodBody(), started_at: NOW - 4 * 3600 - 1 }) as any).status, 400);
});

test('validateCallEnded: the gateway\'s limits: the NEWEST 200 turns, each cut to 2000 characters', { timeout: 8000 }, () => {
  const turns = Array.from({ length: 250 }, (_, i) => ({ role: 'user', text: `line ${i}`, ts: NOW - 30 }));
  turns[249].text = 'é'.repeat(2500);
  const r = validateCallEnded({ ...goodBody(), turns }) as { ok: true; body: { turns: Array<{ text: string }> } };
  assert.ok(r.ok);
  assert.equal(r.body.turns.length, 200);
  assert.equal(r.body.turns[0].text, 'line 50');
  assert.equal(r.body.turns[199].text.length, 2000);
});

test('validateCallEnded: an optional thread_id (item 10) is kept when well-formed, refused (400) when not', { timeout: 8000 }, () => {
  const ok = validateCallEnded({ ...goodBody(), thread_id: 'web_k3j9x2ab' }) as { ok: true; body: any };
  assert.ok(ok.ok);
  assert.equal(ok.body.thread_id, 'web_k3j9x2ab');
  const none = validateCallEnded(goodBody()) as { ok: true; body: any };
  assert.ok(none.ok && !('thread_id' in none.body));                    // absent stays absent
  const nul = validateCallEnded({ ...goodBody(), thread_id: null }) as { ok: true; body: any };
  assert.ok(nul.ok && !('thread_id' in nul.body));                      // null is no thread
  for (const bad of ['', 'has space', 'a/b', 'x'.repeat(81), 'web_1\n', 42, ['web_1'], {}]) {
    const v = validateCallEnded({ ...goodBody(), thread_id: bad }) as { ok: false; status: number; error: string };
    assert.equal(v.ok, false, JSON.stringify(bad));
    assert.equal(v.status, 400);
    assert.match(v.error, /thread_id/);
  }
});

test('POST /call-ended: the thread_id reaches the gateway; a malformed one is a 400 and never forwarded', { timeout: 8000 }, async () => {
  const app3 = express();
  app3.use(express.json());
  app3.use('/api/voice', createVoiceNativeRouter());
  const s3 = app3.listen(0, '127.0.0.1');
  await new Promise<void>((r3) => s3.once('listening', () => r3()));
  try {
    const url = `http://127.0.0.1:${(s3.address() as AddressInfo).port}/api/voice/call-ended`;
    const post = (body: unknown) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', cookie: sessionCookie() }, body: JSON.stringify(body) });
    lastPost = {};
    const res = await post({ ...goodBody('native-thread000001'), thread_id: 'web_k3j9x2ab' });
    assert.equal(res.status, 200);
    assert.deepEqual(lastPost.body, { ...goodBody('native-thread000001'), thread_id: 'web_k3j9x2ab' });
    lastPost = {};
    const bad = await post({ ...goodBody('native-thread000002'), thread_id: 'no spaces allowed' });
    assert.equal(bad.status, 400);
    assert.deepEqual(lastPost, {});
  } finally { s3.close(); }
});

test('fitReason: cut on a character boundary to <= 123 UTF-8 bytes', { timeout: 8000 }, () => {
  const r = fitReason('é'.repeat(100));                 // 200 bytes
  assert.ok(Buffer.byteLength(r) <= 123);
  assert.equal(r, 'é'.repeat(61));
  assert.ok(!r.includes('�'));
  assert.equal(fitReason('short'), 'short');
});

test('ConvSeen: a repeat inside the TTL is refused; after the TTL, or a release, it is new again', { timeout: 8000 }, () => {
  let t = 0;
  const seen = new ConvSeen(1000, () => t);
  assert.equal(seen.claim('native-a'), true);
  assert.equal(seen.claim('native-a'), false);
  t = 1001;
  assert.equal(seen.claim('native-a'), true);
  seen.release('native-a');
  assert.equal(seen.claim('native-a'), true);
});

test('POST /call-ended: 401 without a session; 400 on a bad body (gateway never called)', { timeout: 8000 }, async () => {
  lastPost = {};
  const no = await fetch(`${base}/api/voice/call-ended`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(goodBody()) });
  assert.equal(no.status, 401);
  const bad = await fetch(`${base}/api/voice/call-ended`, { method: 'POST', headers: { 'Content-Type': 'application/json', cookie: sessionCookie() }, body: JSON.stringify({ turns: [] }) });
  assert.equal(bad.status, 400);
  assert.deepEqual(lastPost, {});
});

test('POST /call-ended: forwards the cleaned body with the fleet bearer; a repeat is 409; a refused post may retry', { timeout: 8000 }, async () => {
  // The real deps (gateway URL from WATCH_GATEWAY_URL, bearer from the token file) against the stand-in gateway.
  const r = await realCallEndedDeps.post((validateCallEnded(goodBody()) as any).body, realCallEndedDeps.token());
  assert.deepEqual(r, { status: 200, json: { ok: true, call_id: 'vc_client_0123456789ab' } });
  assert.equal(lastPost.auth, `Bearer ${BEARER}`);
  // Through the mounted route (real deps by default).
  const app2 = express();
  app2.use(express.json());
  app2.use('/api/voice', createVoiceNativeRouter());
  const s2 = app2.listen(0, '127.0.0.1');
  await new Promise<void>((r2) => s2.once('listening', () => r2()));
  try {
    const url = `http://127.0.0.1:${(s2.address() as AddressInfo).port}/api/voice/call-ended`;
    const post = (body: unknown) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', cookie: sessionCookie() }, body: JSON.stringify(body) });
    lastPost = {};
    const res = await post({ ...goodBody(), extra: 'dropped' });
    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), { ok: true, call_id: 'vc_client_0123456789ab' });
    assert.equal(lastPost.auth, `Bearer ${BEARER}`);
    assert.deepEqual(lastPost.body, goodBody());          // the extra field never reaches the gateway
    lastPost = {};
    const again = await post(goodBody());
    assert.equal(again.status, 409);
    assert.deepEqual(lastPost, {});                       // the gateway never sees the repeat
    postStatus = 400;
    const refused = await post(goodBody('native-bbbbbbbbbbbb'));
    assert.equal(refused.status, 400);
    assert.deepEqual(await refused.json(), { ok: false, error: 'nope' });
    postStatus = 200;
    const retry = await post(goodBody('native-bbbbbbbbbbbb'));
    assert.equal(retry.status, 200);
  } finally { postStatus = 200; s2.close(); }
});
