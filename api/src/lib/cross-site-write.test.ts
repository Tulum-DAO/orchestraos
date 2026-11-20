/** RED-first: a write another site started is refused; reads and same-origin/non-browser writes pass. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { crossSiteWriteGuard, isCrossSiteWrite, isForeignOriginWrite } from './cross-site-write.js';

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
  app.use(crossSiteWriteGuard((o) => o === 'https://own.example'));
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
    const oldSafari = await fetch(`${base}/api/x`, { method: 'POST', body: 'x',
      headers: { 'content-type': 'text/plain', origin: 'https://evil.example' } });
    assert.equal(oldSafari.status, 403, 'no Sec-Fetch-Site + a foreign Origin is refused');
    assert.equal(reached, 1);
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
  const guard = src.indexOf('app.use(crossSiteWriteGuard(');
  assert.ok(guard > 0, 'server.ts must app.use(crossSiteWriteGuard(...))');
  assert.match(src.slice(guard, guard + 200), /corsOriginDecision\(/, 'the guard uses the CORS origin decision');
  assert.ok(guard < src.indexOf("app.use(express.json("), 'before express.json');
  assert.ok(guard < src.indexOf("app.use('/api/"), 'before the first /api router');
});

test('no Sec-Fetch-Site (old Safari): judged by Origin through the CORS decision', () => {
  const ok = (o: string) => o === 'https://own.example';
  assert.equal(isForeignOriginWrite('POST', undefined, 'https://evil.example', ok), true);
  assert.equal(isForeignOriginWrite('POST', undefined, 'null', ok), true);
  assert.equal(isForeignOriginWrite('POST', undefined, 'https://own.example', ok), false);
  assert.equal(isForeignOriginWrite('POST', undefined, undefined, ok), false, 'no Origin = not a browser');
  assert.equal(isForeignOriginWrite('GET', undefined, 'https://evil.example', ok), false);
  assert.equal(isForeignOriginWrite('POST', 'same-origin', 'https://evil.example', ok), false, 'Sec-Fetch-Site wins when present');
});
