import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { IncomingMessage } from 'http';
import { createRequire } from 'module';
import { tailscaleOperator, allowedTailscaleUsers, parseTailscaleUsers, tailscaleRole } from './tailscale-identity.js';

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

test('a line may name a role after the login; the old one-login-per-line file still reads', () => {
  assert.deepEqual(parseTailscaleUsers('sam@x.net admin\nmo@y.net member\n# a comment\nold@z.net\n'), [
    { login: 'sam@x.net', role: 'admin' }, { login: 'mo@y.net', role: 'member' }, { login: 'old@z.net', role: null }]);
  assert.deepEqual(parseTailscaleUsers('a@x.net, B@y.net'), [{ login: 'a@x.net', role: null }, { login: 'b@y.net', role: null }]);
  assert.deepEqual(parseTailscaleUsers('a@x.net member # left the team?'), [{ login: 'a@x.net', role: 'member' }]);
  assert.deepEqual(parseTailscaleUsers('member\nc@x.net'), [{ login: 'c@x.net', role: null }], 'a role with no login before it on its line is ignored');
});

test('a role word is never a login: the allowlist is logins only', () => {
  process.env.ORCHESTRA_TAILSCALE_USERS = 'a@x.net admin, b@y.net member';
  try { assert.deepEqual(allowedTailscaleUsers(), ['a@x.net', 'b@y.net']); }
  finally { delete process.env.ORCHESTRA_TAILSCALE_USERS; }
});

test('a listed login with no role is admin; a login not on the list is a member (always member)', () => {
  const users = parseTailscaleUsers('sam@x.net\nmo@y.net member');
  assert.equal(tailscaleRole('sam@x.net', users), 'admin');
  assert.equal(tailscaleRole('MO@y.net', users), 'member');
  assert.equal(tailscaleRole('who@z.net', users), 'member');
  assert.equal(tailscaleRole('who@z.net', []), 'member', 'no file at all: nobody is admin');
});

test('a login listed twice takes the least privilege: any member wins', () => {
  assert.deepEqual(parseTailscaleUsers('a@x.net\na@x.net member'), [{ login: 'a@x.net', role: 'member' }]);
  assert.deepEqual(parseTailscaleUsers('a@x.net member\nA@x.net admin'), [{ login: 'a@x.net', role: 'member' }]);
  assert.deepEqual(parseTailscaleUsers('a@x.net admin\na@x.net'), [{ login: 'a@x.net', role: 'admin' }]);
  assert.deepEqual(parseTailscaleUsers('a@x.net admin member'), [{ login: 'a@x.net', role: 'member' }]);
  assert.equal(tailscaleRole('a@x.net', parseTailscaleUsers('a@x.net admin\nb@y.net\na@x.net member')), 'member');
});

test('a role written before the login belongs to it', () => {
  assert.deepEqual(parseTailscaleUsers('member a@x.net'), [{ login: 'a@x.net', role: 'member' }]);
  assert.deepEqual(parseTailscaleUsers('admin a@x.net, b@y.net'), [{ login: 'a@x.net', role: 'admin' }, { login: 'b@y.net', role: null }]);
});

test('an unknown extra word makes that line\'s login a member, is logged, and is never a login', () => {
  const warned: string[] = [];
  const orig = console.warn;
  console.warn = (...a: unknown[]) => { warned.push(a.join(' ')); };
  try {
    assert.deepEqual(parseTailscaleUsers('a@x.net admni\nb@y.net'), [{ login: 'a@x.net', role: 'member' }, { login: 'b@y.net', role: null }]);
    assert.deepEqual(parseTailscaleUsers('owner c@x.net'), [{ login: 'c@x.net', role: 'member' }]);
    assert.deepEqual(parseTailscaleUsers('d@x.net admin typo'), [{ login: 'd@x.net', role: 'member' }]);
  } finally { console.warn = orig; }
  assert.ok(warned.some((w) => w.includes('admni') && w.includes('a@x.net')), `warned: ${JSON.stringify(warned)}`);
  process.env.ORCHESTRA_TAILSCALE_USERS = 'a@x.net admni, b@y.net';
  try { assert.deepEqual(allowedTailscaleUsers(), ['a@x.net', 'b@y.net']); }
  finally { delete process.env.ORCHESTRA_TAILSCALE_USERS; }
});

// The dashboard proxy (web terminal) reads the same file with its own copy of the allowlist half: both run on the same
// fixtures so the two parsers cannot drift.
test('the dashboard proxy allows exactly the logins the API allows', () => {
  const require = createRequire(import.meta.url);
  const { allowedLogins } = require('../../../dashboard-tailscale-users.js') as { allowedLogins: (raw: string) => string[] };
  const FIXTURES = [
    '', 'sam@x.net', 'a@x.net, B@y.net', 'sam@x.net admin\nmo@y.net member\n# a comment\nold@z.net\n',
    'a@x.net member # left the team?', 'member\nc@x.net', 'a@x.net\na@x.net member', 'member a@x.net',
    'a@x.net admni\nb@y.net', 'owner c@x.net', 'admin, member', 'a@x.net admin, b@y.net member', 'A@X.net\r\na@x.net\n',
  ];
  const orig = console.warn;
  console.warn = () => {};
  try {
    for (const f of FIXTURES) assert.deepEqual(allowedLogins(f), parseTailscaleUsers(f).map((u) => u.login), JSON.stringify(f));
  } finally { console.warn = orig; }
});
