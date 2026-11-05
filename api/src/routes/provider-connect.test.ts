/**
 * /api/providers/:id/connect: admin only, known providers only, codes validated, wired to the manager.
 * The manager runs on a fake tmux / exec layer: nothing real is installed or signed in.
 * Run: npx tsx --test src/routes/provider-connect.test.ts   (from api/)
 */
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { createProviderConnectRouter } from './provider-connect.js';
import { ConnectManager, type ConnectDeps } from '../lib/providerConnect/manager.js';
import { signinScript } from '../lib/providerConnect/specs.js';
import { AGY_URL_PANE } from '../lib/providerConnect/fixtures.js';

const panes = new Map<string, string>();
const sent: string[] = [];
const deps: ConnectDeps = {
  now: () => Date.now(),
  tmux: {
    has: async (s) => panes.has(s),
    start: async (s) => { panes.set(s, ''); return true; },
    capture: async (s) => panes.get(s) ?? '',
    sendLiteral: async (_s, t) => { sent.push(t); },
    sendKey: async (_s, k) => { sent.push(k); },
    kill: async (s) => { panes.delete(s); },
    mark: async () => {}, isOurs: async () => false,
  },
  signedIn: async () => false, binInstalled: () => true, visible: () => true, preflight: async () => null,
  install: async () => ({ ok: true, log: '' }), ensureLink: () => {}, version: async () => '1.5.0',
  signinCwd: () => '/tmp/x', signinScript, refreshRuntimes: () => {},
};
const manager = new ConnectManager(deps);
let role: 'admin' | 'member' = 'admin';
const app = express();
app.use(express.json());
app.use('/api/providers', createProviderConnectRouter({ manager, role: () => role }));
const server = app.listen(0);
after(() => server.close());
const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/providers`;
const call = async (method: string, p: string, body?: unknown) => {
  const r = await fetch(`${base}${p}`, { method, headers: { 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  return { status: r.status, body: await r.json() as any };
};

test('a member is refused every endpoint with not_admin', async () => {
  role = 'member';
  for (const [m, p] of [['GET', '/codex/connect'], ['POST', '/codex/connect'], ['POST', '/gemini/connect/code'], ['DELETE', '/grok/connect'], ['GET', '/grok/connect/output']]) {
    const r = await call(m, p, m === 'GET' ? undefined : {});
    assert.equal(r.status, 403, `${m} ${p}`);
    assert.equal(r.body.reason, 'not_admin');
  }
  role = 'admin';
});

test('unknown providers are a 400 (claude keeps its own sign-in)', async () => {
  for (const id of ['claude', 'x', '__proto__']) assert.equal((await call('GET', `/${id}/connect`)).status, 400);
});

test('idle, then start, then the same attempt on a second start', async () => {
  assert.deepEqual((await call('GET', '/grok/connect')).body, { ok: true, provider: 'grok', phase: 'idle' });
  const a = await call('POST', '/grok/connect', {});
  assert.equal(a.body.phase, 'preflight');
  await manager.settle();
  const b = await call('POST', '/grok/connect', {});
  assert.equal(b.body.attemptId, a.body.attemptId);
  assert.equal(b.body.phase, 'starting');
  const c = await call('POST', '/grok/connect', { fresh: true });
  assert.notEqual(c.body.attemptId, a.body.attemptId);
  await manager.settle();
  assert.equal((await call('DELETE', '/grok/connect', {})).body.cancelled, true);
  assert.equal((await call('GET', '/grok/connect')).body.phase, 'idle');
});

test('gemini code: refused before agy asks, a bad charset is a 400, a good one is typed', async () => {
  assert.equal((await call('POST', '/gemini/connect/code', { code: '4/0AVG7fiQ_abc' })).status, 409);
  await call('POST', '/gemini/connect', {});
  await manager.settle();
  panes.set('login-agy', AGY_URL_PANE);
  await manager.tick();
  assert.equal((await call('GET', '/gemini/connect')).body.needsCode, true);
  const bad = await call('POST', '/gemini/connect/code', { code: '4/0A`reboot`' });
  assert.deepEqual([bad.status, bad.body.reason], [400, 'bad_code']);
  const ok = await call('POST', '/gemini/connect/code', { code: '4/0AVG7fiQ_abcDEF' });
  assert.equal(ok.body.phase, 'checking_code');
  assert.deepEqual(sent.slice(-2), ['4/0AVG7fiQ_abcDEF', 'Enter']);
  assert.match((await call('GET', '/gemini/connect/output')).body.output, /accounts\.google\.com/);
  await call('DELETE', '/gemini/connect', {});
});

test('mutations without a JSON content type are refused with 415', async () => {
  for (const [m, p] of [['POST', '/grok/connect'], ['POST', '/gemini/connect/code'], ['DELETE', '/grok/connect']]) {
    const r = await fetch(`${base}${p}`, { method: m, headers: { 'content-type': 'text/plain' }, body: 'x' });
    assert.equal(r.status, 415, `${m} ${p}`);
    const none = await fetch(`${base}${p}`, { method: m });
    assert.equal(none.status, 415, `${m} ${p} (no body)`);
  }
  assert.equal((await call('GET', '/grok/connect')).status, 200);   // reads need no body
});
