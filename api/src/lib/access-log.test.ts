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
