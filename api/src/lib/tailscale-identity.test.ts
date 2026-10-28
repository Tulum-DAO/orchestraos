import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { IncomingMessage } from 'http';
import { tailscaleOperator, allowedTailscaleUsers } from './tailscale-identity.js';

const req = (addr: string, login?: string) =>
  ({ socket: { remoteAddress: addr }, headers: login ? { 'tailscale-user-login': login } : {} }) as unknown as IncomingMessage;

test('an allowlisted tailnet login arriving through the local proxy is the operator', () => {
  assert.equal(tailscaleOperator(req('127.0.0.1', 'Operator@Example.net'), ['operator@example.net']), true);
});
test('a login not on the list, a missing header, or a non-loopback peer is not', () => {
  assert.equal(tailscaleOperator(req('127.0.0.1', 'mallory@example.net'), ['operator@example.net']), false);
  assert.equal(tailscaleOperator(req('127.0.0.1'), ['operator@example.net']), false);
  assert.equal(tailscaleOperator(req('100.64.0.9', 'operator@example.net'), ['operator@example.net']), false);
});
test('no allowlist means nobody', () => {
  assert.equal(tailscaleOperator(req('127.0.0.1', 'operator@example.net'), []), false);
});
test('the env allowlist is comma or space separated', () => {
  process.env.ORCHESTRA_TAILSCALE_USERS = 'a@example.com, B@example.org';
  try { assert.deepEqual(allowedTailscaleUsers(), ['a@example.com', 'b@example.org']); }
  finally { delete process.env.ORCHESTRA_TAILSCALE_USERS; }
});
