/**
 * /api/away + /api/presence: the web dashboard's path to the away card (RFC 0002). Pure over an injected
 * fetch; nothing here opens a socket to a real gateway.
 * Run: npx tsx --test src/routes/away.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';
import { createAwayRouter, type AwayDeps } from './away.js';

type Call = { url: string; init: RequestInit };

function makeDeps(over: Partial<AwayDeps> = {}): AwayDeps & { calls: Call[] } {
  const calls: Call[] = [];
  const deps: any = {
    token: () => 'test-token',
    gatewayUrl: () => 'http://gw.test',
    fetchJson: async (url: string, init: RequestInit) => { calls.push({ url, init }); return { status: 200, body: { ok: true } }; },
    ...over,
  };
  deps.calls = calls;
  return deps;
}

async function req(deps: AwayDeps, method: string, path: string, body?: unknown) {
  const app = express();
  app.use(express.json());
  app.use('/api', createAwayRouter(deps));
  const server = app.listen(0);
  const port = (server.address() as any).port;
  try {
    return await new Promise<{ status: number; json: any }>((resolve, reject) => {
      const data = body === undefined ? undefined : JSON.stringify(body);
      const r = http.request({ host: '127.0.0.1', port, path, method,
        headers: data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {} }, (res) => {
        let out = '';
        res.on('data', (c) => (out += c));
        res.on('end', () => resolve({ status: res.statusCode || 0, json: JSON.parse(out || '{}') }));
      });
      r.on('error', reject);
      if (data) r.write(data);
      r.end();
    });
  } finally {
    server.close();
  }
}

test('GET /api/away forwards the anchor with the gateway bearer', async () => {
  const deps = makeDeps();
  const r = await req(deps, 'GET', '/api/away?anchor=screen');
  assert.equal(r.status, 200);
  assert.equal(deps.calls[0].url, 'http://gw.test/away?anchor=screen');
  assert.equal((deps.calls[0].init.headers as any).Authorization, 'Bearer test-token');
});

test('GET /api/away refuses an unknown anchor without calling the gateway', async () => {
  const deps = makeDeps();
  const r = await req(deps, 'GET', '/api/away?anchor=../../x');
  assert.equal(r.status, 400);
  assert.equal(deps.calls.length, 0);
});

test('POST /api/presence forwards only foreground/web/client_id/label: never viewing', async () => {
  const deps = makeDeps();
  await req(deps, 'POST', '/api/presence', { client_id: 'c-1', label: 'Chrome on macOS', viewing: 'gm', surface: 'phone' });
  const sent = JSON.parse(String(deps.calls[0].init.body));
  assert.deepEqual(sent, { foreground: true, surface: 'web', client_id: 'c-1', label: 'Chrome on macOS' });
});

test('upstream status passes through; no token is a 502', async () => {
  const notFound = makeDeps({ fetchJson: async () => ({ status: 404, body: { ok: false } }) });
  assert.equal((await req(notFound, 'GET', '/api/away')).status, 404);
  const noTok = makeDeps({ token: () => '' });
  assert.equal((await req(noTok, 'GET', '/api/away')).status, 502);
  assert.equal(noTok.calls.length, 0);
});
