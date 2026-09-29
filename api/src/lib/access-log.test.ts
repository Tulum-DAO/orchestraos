/**
 * The detection blind spot this closes: api.log held ZERO client addresses while the API
 * was bound to every interface with fail-open admin defaults, so "did any of this come
 * from another host?" was unanswerable after the fact.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Request } from 'express';
import { describe as describeReq, format, isLoopback } from './access-log.js';

const req = (o: {
  addr?: string; method?: string; url?: string; headers?: Record<string, any>;
} = {}) => ({
  method: o.method ?? 'GET',
  url: o.url ?? '/api/me',
  originalUrl: o.url ?? '/api/me',
  headers: o.headers ?? {},
  socket: { remoteAddress: o.addr ?? '127.0.0.1' },
} as unknown as Request);

test('loopback detection covers v4, v6 and the v4-mapped-v6 form', () => {
  assert.equal(isLoopback('127.0.0.1'), true);
  assert.equal(isLoopback('::1'), true);
  assert.equal(isLoopback('::ffff:127.0.0.1'), true, 'dual-stack sockets report this form');
  assert.equal(isLoopback('192.168.100.171'), false);
  assert.equal(isLoopback(undefined), false);
});

test('an ordinary loopback GET is NOT logged', () => {
  assert.equal(describeReq(req()), null,
    'logging every dashboard poll buries the interesting lines in noise');
});

test('a request from another host IS logged — the question triage could not answer', () => {
  const e = describeReq(req({ addr: '192.168.100.171' }));
  assert.ok(e, 'a non-loopback request went unrecorded');
  assert.equal(e!.addr, '192.168.100.171');
  assert.ok(e!.reasons.includes('non-loopback'));
});

test('an identity header is logged with its claimed value', () => {
  const e = describeReq(req({ headers: { 'x-orchestra-user': 'eve', 'x-orchestra-role': 'admin' } }));
  assert.ok(e);
  assert.equal(e!.identity['x-orchestra-user'], 'eve');
  assert.equal(e!.identity['x-orchestra-role'], 'admin');
  assert.ok(format(e!).includes('"eve"'), 'the spoofed claim must appear in the line');
});

test('a mutating request is logged even from loopback with no headers', () => {
  const e = describeReq(req({ method: 'POST', url: '/api/agents/gm/send' }));
  assert.ok(e, 'writes are exactly what needs attributing after an incident');
  assert.ok(e!.reasons.includes('mutating'));
  assert.equal(e!.path, '/api/agents/gm/send');
});

test('credentials are recorded as presence only, never by value', () => {
  const e = describeReq(req({
    method: 'POST',
    headers: { authorization: 'Bearer super-secret-token', cookie: 'session=abc123' },
  }))!;
  const line = format(e);
  assert.ok(!line.includes('super-secret-token'), 'token value leaked into the audit log');
  assert.ok(!line.includes('abc123'), 'cookie value leaked into the audit log');
  assert.ok(line.includes('credentials_present=authorization,cookie'));
});

test('x-forwarded-for is recorded but explicitly marked as a claim', () => {
  const e = describeReq(req({ addr: '10.0.0.5', headers: { 'x-forwarded-for': '1.2.3.4' } }))!;
  assert.equal(e.forwardedFor, '1.2.3.4');
  const line = format(e);
  assert.ok(line.includes('addr=10.0.0.5'), 'the real peer must still be the primary field');
  assert.ok(line.includes('claimed'), 'a spoofable header must not read as fact');
});

test('the query string is stripped from the logged path', () => {
  const e = describeReq(req({ method: 'POST', url: '/api/x?token=leaky' }))!;
  assert.equal(e.path, '/api/x');
  assert.ok(!format(e).includes('leaky'));
});

test('an array-valued identity header is flattened, not dropped', () => {
  const e = describeReq(req({ headers: { 'x-orchestra-user': ['alice', 'gm'] } }))!;
  assert.equal(e.identity['x-orchestra-user'], 'alice,gm',
    'a smuggled second identity must still be visible in the log');
});

// ── identity namespace is an allowlist, not an open prefix ─────────────────
// review found this gating fc55a89: logging every x-orchestra-* header BY VALUE is a
// latent trap for the authenticating-proxy work principal.ts anticipates — a future
// x-orchestra-signature would have landed in the audit log in cleartext.

test('the four known identity headers are still logged by value', () => {
  const e = describeReq(req({ headers: {
    'x-orchestra-user': 'alice', 'x-orchestra-role': 'admin',
    'x-orchestra-client': 'acme', 'x-orchestra-allowed-agents': 'a,b',
  } }))!;
  assert.equal(e.identity['x-orchestra-user'], 'alice');
  assert.equal(e.identity['x-orchestra-role'], 'admin');
  assert.equal(e.identity['x-orchestra-client'], 'acme');
  assert.equal(e.identity['x-orchestra-allowed-agents'], 'a,b');
  assert.deepEqual(e.unloggedIdentity, []);
});

test('a future proxy signature header is NOT logged by value', () => {
  const e = describeReq(req({ headers: {
    'x-orchestra-signature': 'deadbeefcafe', 'x-orchestra-proxy-token': 'hunter2',
  } }))!;
  const line = format(e);
  assert.ok(!line.includes('deadbeefcafe'), 'signature value leaked into the audit log');
  assert.ok(!line.includes('hunter2'), 'proxy token value leaked into the audit log');
});

test('an unknown identity header is still recorded as PRESENT', () => {
  // An allowlist alone would drop it silently; an unexpected header in this namespace
  // is exactly the signal you want during an incident.
  const e = describeReq(req({ headers: { 'x-orchestra-signature': 'deadbeef' } }))!;
  assert.ok(e.unloggedIdentity.includes('x-orchestra-signature'));
  assert.ok(e.reasons.includes('identity-headers'), 'unknown header must still trip the log');
  assert.ok(format(e).includes('identity_headers_unlogged=x-orchestra-signature'));
});

test('known and unknown identity headers coexist correctly', () => {
  const e = describeReq(req({ headers: {
    'x-orchestra-user': 'eve', 'x-orchestra-secret': 's3cr3t', // pragma: allowlist secret
  } }))!;
  const line = format(e);
  assert.ok(line.includes('"eve"'), 'the claim we DO want must survive');
  assert.ok(!line.includes('s3cr3t'), 'the value we do NOT want leaked');
  assert.deepEqual(e.unloggedIdentity, ['x-orchestra-secret']);
});
