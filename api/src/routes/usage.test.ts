/**
 * /api/usage: the dashboard's path to plan usage. Pure over an injected fetch; nothing here opens a
 * socket to a real gateway.
 * Run: npx tsx --test src/routes/usage.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';
import { createUsageRouter, type UsageDeps } from './usage.js';

type Call = { url: string; init: RequestInit };
const ROWS = [{ provider: 'codex', window: '5h', used_pct: 41, stale: false }];

function makeDeps(over: Partial<UsageDeps> = {}): UsageDeps & { calls: Call[] } {
  const calls: Call[] = [];
  const deps: any = {
    token: () => 'test-token',
    gatewayUrl: () => 'http://gw.test',
    fetchJson: async (url: string, init: RequestInit) => {
      calls.push({ url, init }); return { status: 200, body: { ok: true, usage: ROWS } };
    },
    isAdmin: () => true,
    ...over,
  };
  deps.calls = calls;
  return deps;
}

async function get(deps: UsageDeps, path: string) {
  const app = express();
  app.use('/api', createUsageRouter(deps));
  const server = app.listen(0);
  const port = (server.address() as any).port;
  try {
    return await new Promise<{ status: number; json: any }>((resolve, reject) => {
      const r = http.request({ host: '127.0.0.1', port, path, method: 'GET' }, (res) => {
        let out = '';
        res.on('data', (c) => (out += c));
        res.on('end', () => resolve({ status: res.statusCode || 0, json: JSON.parse(out || '{}') }));
      });
      r.on('error', reject);
      r.end();
    });
  } finally {
    server.close();
  }
}

test('GET /api/usage forwards to the gateway with its bearer', async () => {
  const deps = makeDeps();
  const r = await get(deps, '/api/usage');
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.usage, ROWS);
  assert.equal(deps.calls[0].url, 'http://gw.test/usage');
  assert.equal((deps.calls[0].init.headers as any).Authorization, 'Bearer test-token');
});

test('a non-admin (a client user behind the sign-in proxy) gets 404 and the gateway is never called', async () => {
  const deps = makeDeps({ isAdmin: () => false });
  const r = await get(deps, '/api/usage');
  assert.equal(r.status, 404);
  assert.equal(deps.calls.length, 0);
});

test('no gateway token is a 502, not an empty meter', async () => {
  const r = await get(makeDeps({ token: () => '' }), '/api/usage');
  assert.equal(r.status, 502);
});

test('an unreachable gateway is a 502', async () => {
  const r = await get(makeDeps({ fetchJson: async () => { throw new Error('ECONNREFUSED'); } }), '/api/usage');
  assert.equal(r.status, 502);
  assert.equal(r.json.error, 'gateway unreachable');
});
