/** RED-first: a write another site started is refused; reads and same-origin/non-browser writes pass. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { isCrossSiteWrite, refuseCrossSiteWrites } from './cross-site-write.js';

test('cross-site and same-site writes are refused, every write method', () => {
  for (const m of ['POST', 'PUT', 'PATCH', 'DELETE', 'post']) {
    assert.equal(isCrossSiteWrite(m, 'cross-site'), true, m);
    assert.equal(isCrossSiteWrite(m, 'same-site'), true, m);
    assert.equal(isCrossSiteWrite(m, 'Cross-Site'), true, m);
  }
});

test('same-origin, user-initiated and non-browser writes pass', () => {
  assert.equal(isCrossSiteWrite('POST', 'same-origin'), false);
  assert.equal(isCrossSiteWrite('POST', 'none'), false);
  assert.equal(isCrossSiteWrite('POST', undefined), false);
  assert.equal(isCrossSiteWrite('POST', ''), false);
});

test('reads are never refused, whoever started them', () => {
  for (const m of ['GET', 'HEAD', 'OPTIONS']) assert.equal(isCrossSiteWrite(m, 'cross-site'), false, m);
});

test('a duplicated header with any foreign value is refused', () => {
  assert.equal(isCrossSiteWrite('POST', ['same-origin', 'cross-site']), true);
  assert.equal(isCrossSiteWrite('POST', 'same-origin, cross-site'), true);
});

test('by effect: a forged text/plain POST never reaches the route; a same-origin one does', async () => {
  let reached = 0;
  const app = express();
  app.use(refuseCrossSiteWrites);
  app.use(express.json());
  app.post('/api/x', (_req, res) => { reached++; res.status(400).json({ error: 'route ran' }); });
  app.get('/api/x', (_req, res) => { reached++; res.json({ ok: true }); });
  const srv = app.listen(0, '127.0.0.1');
  await new Promise((r) => srv.once('listening', r));
  const base = `http://127.0.0.1:${(srv.address() as AddressInfo).port}`;
  try {
    const forged = await fetch(`${base}/api/x`, { method: 'POST', body: 'x',
      headers: { 'content-type': 'text/plain', origin: 'https://evil.example', 'sec-fetch-site': 'cross-site' } });
    assert.equal(forged.status, 403);
    assert.equal(reached, 0, 'the route must not run for a cross-site write');
    const own = await fetch(`${base}/api/x`, { method: 'POST', body: '{}',
      headers: { 'content-type': 'application/json', 'sec-fetch-site': 'same-origin' } });
    assert.equal(own.status, 400);
    const read = await fetch(`${base}/api/x`, { headers: { 'sec-fetch-site': 'cross-site' } });
    assert.equal(read.status, 200);
    assert.equal(reached, 2);
  } finally {
    srv.close();
  }
});

test('wiring: server.ts mounts the guard before the body parser and every /api route', async () => {
  const { readFileSync } = await import('node:fs');
  const src = readFileSync(new URL('../server.ts', import.meta.url), 'utf8');
  const guard = src.indexOf('app.use(refuseCrossSiteWrites)');
  assert.ok(guard > 0, 'server.ts must app.use(refuseCrossSiteWrites)');
  assert.ok(guard < src.indexOf("app.use(express.json("), 'before express.json');
  assert.ok(guard < src.indexOf("app.use('/api/"), 'before the first /api router');
});
