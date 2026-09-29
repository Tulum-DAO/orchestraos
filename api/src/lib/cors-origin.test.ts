/**
 * RED-first for the CORS decision.
 *
 * Two faults, found while triaging an unrelated 502 (2026-09-29):
 *  - the log said "blocked origin" for every request the operator made, though NOTHING was
 *    blocked: the page and the API share an origin, so the browser never needed the header
 *    that was withheld. The wording sent triage down a CORS path for a latency bug.
 *  - the allowlist is built from [dashboard] host/port, which is the LOOPBACK address. An
 *    install reached at its real address (a Tailscale name, a tunnel, a reverse proxy) is
 *    not in its own allowlist, so a genuinely cross-origin call from it would fail.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { originDecision, publicOrigins } from './cors-origin.js';

const ALLOW = ['http://127.0.0.1:8891', 'https://127.0.0.1:8891'];

test('a non-browser caller (no Origin) is allowed', () => {
  const d = originDecision({ allowlist: ALLOW });
  assert.equal(d.allow, true);
  assert.equal(d.reason, 'no-origin');
});

test('an allowlisted origin is allowed', () => {
  const d = originDecision({ origin: 'http://127.0.0.1:8891', allowlist: ALLOW });
  assert.equal(d.allow, true);
  assert.equal(d.reason, 'allowlisted');
});

// The operator's case: the dashboard is served from the SAME address the API is reached
// at, so the request is same-origin and CORS never applied to it.
test('an origin matching the address the request was sent to is same-origin', () => {
  const d = originDecision({
    origin: 'https://srv1397016.tail8be541.ts.net:18891',
    forwardedHost: 'srv1397016.tail8be541.ts.net:18891',
    allowlist: ALLOW,
  });
  assert.equal(d.allow, true);
  assert.equal(d.sameOrigin, true);
});

test('the Host header is used when no proxy forwarded one', () => {
  const d = originDecision({
    origin: 'http://box.local:8891',
    hostHeader: 'box.local:8891',
    allowlist: ALLOW,
  });
  assert.equal(d.allow, true);
  assert.equal(d.sameOrigin, true);
});

test('a DIFFERENT origin is still refused, even with a forwarded host present', () => {
  const d = originDecision({
    origin: 'https://evil.example',
    forwardedHost: 'srv1397016.tail8be541.ts.net:18891',
    allowlist: ALLOW,
  });
  assert.equal(d.allow, false);
  assert.equal(d.sameOrigin, false);
  assert.equal(d.reason, 'not-allowlisted');
});

test('a host that merely ENDS WITH the real one does not pass as same-origin', () => {
  const d = originDecision({
    origin: 'https://evil-srv1397016.tail8be541.ts.net:18891',
    forwardedHost: 'srv1397016.tail8be541.ts.net:18891',
    allowlist: ALLOW,
  });
  assert.equal(d.allow, false);
});

test('a malformed Origin is refused rather than throwing', () => {
  assert.equal(originDecision({ origin: 'not a url', allowlist: ALLOW }).allow, false);
});

// [public].host is documented as "the externally-reachable URL for this install" — which is
// exactly the address a browser uses, so it belongs in the allowlist.
test('publicOrigins accepts a full URL', () => {
  assert.deepEqual(publicOrigins('https://srv1397016.tail8be541.ts.net:18891'),
                   ['https://srv1397016.tail8be541.ts.net:18891']);
});

test('publicOrigins takes a bare host as either scheme', () => {
  assert.deepEqual(publicOrigins('box.example:8891'),
                   ['http://box.example:8891', 'https://box.example:8891']);
});

test('publicOrigins on a blank or nonsense value yields nothing', () => {
  assert.deepEqual(publicOrigins(''), []);
  assert.deepEqual(publicOrigins('   '), []);
});

test('publicOrigins drops any path — an origin is scheme://host:port only', () => {
  assert.deepEqual(publicOrigins('https://box.example:8891/dashboard/'),
                   ['https://box.example:8891']);
});

// Review finding (2026-09-29): x-forwarded-host is client-settable, so same-origin must not
// be derivable from it unless a trusted peer sent it. This module takes trust as an INPUT —
// server.ts passes forwardedHost only for a loopback peer — so the contract to keep here is
// that an absent forwardedHost cannot be conjured from anything else.

test('with no forwarded host, a foreign origin cannot claim same-origin', () => {
  const d = originDecision({
    origin: 'https://evil.example',
    hostHeader: 'srv1397016.tail8be541.ts.net:18891',
    allowlist: ALLOW,
  });
  assert.equal(d.allow, false);
  assert.equal(d.sameOrigin, false);
});

test('an untrusted forwarded host is simply not passed — and then nothing matches', () => {
  // what server.ts does for a NON-loopback peer: forwardedHost omitted entirely
  const d = originDecision({ origin: 'https://attacker.example', allowlist: ALLOW });
  assert.equal(d.allow, false);
});
