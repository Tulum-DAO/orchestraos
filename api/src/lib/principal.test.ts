/**
 * Regression tests for the 2026-09-29 identity-spoofing P0.
 *
 * Proven exploitable before the fix:
 *   curl /api/me -H "X-Orchestra-User: eve" -> {"role":"admin","allowed_agents":"*"}
 * and agent-send used the same header as the msg_store from_agent, so any local caller
 * could post instructions to every seat attributed to gm.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Request } from 'express';
import { principal, actingAgent, tenantScope, trustsIdentityHeaders, installRole, changedBy } from './principal.js';

const req = (headers: Record<string, string> = {}) => ({ headers } as unknown as Request);

const untrusted = (fn: () => void) => {
  const prev = process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
  delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
  try { fn(); } finally { if (prev !== undefined) process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = prev; }
};

const trusted = (fn: () => void) => {
  const prev = process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
  process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = '1';
  try { fn(); } finally {
    if (prev === undefined) delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
    else process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = prev;
  }
};

// ── untrusted: today's deployment, no proxy exists ──────────────────────────

test('untrusted mode ignores a spoofed username entirely', () => {
  untrusted(() => {
    const p = principal(req({ 'x-orchestra-user': 'eve' }));
    assert.ok(p);
    assert.notEqual(p!.username, 'eve', 'the caller chose their own identity — the exploit');
    assert.equal(p!.trusted, false);
  });
});

test('untrusted mode will not let a caller be attributed as gm', () => {
  untrusted(() => {
    const who = actingAgent(req({ 'x-orchestra-user': 'gm' }));
    assert.notEqual(who, 'gm',
      'a local caller could post instructions to every seat as gm');
  });
});

test('untrusted mode ignores a claimed role', () => {
  untrusted(() => {
    // Role is not attacker-controlled: it comes from config, not the header.
    const a = principal(req({ 'x-orchestra-role': 'admin' }))!.role;
    const b = principal(req({}))!.role;
    assert.equal(a, b, 'the header changed the role in untrusted mode');
  });
});

// ── trusted: a proxy authenticates and overwrites the headers ───────────────

test('trusted mode fails closed with no identity', () => {
  trusted(() => {
    assert.equal(principal(req({})), null, 'absent identity must not yield a principal');
    assert.equal(actingAgent(req({})), null);
    assert.equal(tenantScope(req({})).isAdmin, false, 'no identity must never be admin');
  });
});

test('trusted mode: absent role is NOT admin', () => {
  trusted(() => {
    const p = principal(req({ 'x-orchestra-user': 'eve' }))!;
    assert.equal(p.role, '', 'role defaulted to something');
    assert.equal(tenantScope(req({ 'x-orchestra-user': 'eve' })).isAdmin, false);
  });
});

test('trusted mode: absent agent scope is empty, not wildcard', () => {
  trusted(() => {
    const p = principal(req({ 'x-orchestra-user': 'eve' }))!;
    assert.deepEqual(p.allowedAgents, [], 'absent scope granted wildcard access');
  });
});

test('trusted mode still honours an explicit scope', () => {
  trusted(() => {
    assert.equal(principal(req({ 'x-orchestra-user': 'e', 'x-orchestra-allowed-agents': '*' }))!.allowedAgents, '*');
    assert.deepEqual(
      principal(req({ 'x-orchestra-user': 'e', 'x-orchestra-allowed-agents': '["a","b"]' }))!.allowedAgents,
      ['a', 'b']);
    assert.deepEqual(
      principal(req({ 'x-orchestra-user': 'e', 'x-orchestra-allowed-agents': 'a, b' }))!.allowedAgents,
      ['a', 'b']);
  });
});

test('trusted mode attributes to the proxy-asserted user', () => {
  trusted(() => {
    assert.equal(actingAgent(req({ 'x-orchestra-user': 'alice' })), 'alice');
  });
});

test('an array-valued header cannot smuggle a second identity', () => {
  trusted(() => {
    const r = { headers: { 'x-orchestra-user': ['alice', 'gm'] } } as unknown as Request;
    assert.equal(actingAgent(r), 'alice', 'took something other than the first value');
  });
});

test('the trust flag is off unless explicitly enabled', () => {
  untrusted(() => assert.equal(trustsIdentityHeaders(), false));
  trusted(() => assert.equal(trustsIdentityHeaders(), true));
});

const tsReq = (login?: string, addr = '127.0.0.1') =>
  ({ headers: login ? { 'tailscale-user-login': login } : {}, socket: { remoteAddress: addr } }) as unknown as Request;
const USERS = [{ login: 'sam@x.net', role: null }, { login: 'mo@y.net', role: 'member' as const }];

test('install role: a listed login with no role is admin; a member is a member; an unlisted login or no login is a member', () => {
  untrusted(() => {
    assert.equal(installRole(tsReq('sam@x.net'), USERS), 'admin');
    assert.equal(installRole(tsReq('Mo@Y.net'), USERS), 'member');
    assert.equal(installRole(tsReq('who@z.net'), USERS), 'member');
    assert.equal(installRole(tsReq(), USERS), 'member', 'a caller with no Tailscale login is never admin');
    assert.equal(installRole(tsReq(undefined, '::1'), USERS), 'member');
  });
});

test('install role: the login header only counts when it arrived through the local proxy', () => {
  untrusted(() => {
    assert.equal(installRole(tsReq('sam@x.net', '100.64.0.9'), USERS), 'member');
    assert.equal(installRole(tsReq('mo@y.net', '100.64.0.9'), USERS), 'member');
  });
});

test('install role in trusted mode follows the proxy role and never defaults to admin', () => {
  trusted(() => {
    assert.equal(installRole(req({ 'x-orchestra-user': 'a', 'x-orchestra-role': 'admin' }), USERS), 'admin');
    assert.equal(installRole(req({ 'x-orchestra-user': 'a' }), USERS), 'member');
    assert.equal(installRole(req({}), USERS), 'member');
  });
});

test('changedBy names the Tailscale login when there is one, else the principal', () => {
  untrusted(() => {
    assert.equal(changedBy(tsReq('Sam@X.net')), 'sam@x.net');
    assert.ok(changedBy(tsReq()).length > 0);
  });
});

test('the tenant role is untouched: a member login is still the operator for tenant scoping', () => {
  untrusted(() => assert.equal(tenantScope(tsReq('mo@y.net')).isAdmin, true));
});
