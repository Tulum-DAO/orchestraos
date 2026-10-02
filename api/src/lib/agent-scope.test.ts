/**
 * RED-first: every /api/agents/:id route enforces the same scope GET / does.
 *
 * Found 2026-10-02 while reviewing the lineage-nodes design. GET / narrows the fleet to the
 * caller's principal scope (agents.ts, "fail closed, not wide open"). The /:id routes did not:
 * 13 handlers in agents.ts plus /:id/transcript, /:id/transcript/stream and /:id/send had no
 * scope check at all, and 9 of them mutate — spawn, kill, inject, inject-raw, key, agent-key,
 * PUT prompt, task, send. Under a trusted proxy, a principal scoped to one client could read,
 * kill or type into ANY agent by id.
 *
 * In the default untrusted mode principal() is an admin with '*', so nothing changes there; the
 * untrusted test below asserts that, because a scope fix that breaks the single-operator install
 * would be worse than the gap.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Request } from 'express';
import { canPrincipalSeeAgent, coerceTags, makeAgentScopeParam } from './agent-scope.js';
import type { Principal } from './principal.js';

const P = (over: Partial<Principal>): Principal => ({
  username: 'u', role: 'viewer', allowedAgents: [], clientScope: null, trusted: true, ...over,
});

// ---- the rule, identical to GET / -----------------------------------------------------------

test('a null principal sees nothing — no identity, no access', () => {
  assert.equal(canPrincipalSeeAgent(null, { id: 'gm' }), false);
});

test("'*' sees every agent", () => {
  assert.equal(canPrincipalSeeAgent(P({ allowedAgents: '*' }), { id: 'gm' }), true);
});

test('an explicit allowlist sees only its members', () => {
  const p = P({ allowedAgents: ['pm-elysian', 'build'] });
  assert.equal(canPrincipalSeeAgent(p, { id: 'build' }), true);
  assert.equal(canPrincipalSeeAgent(p, { id: 'gm' }), false);
});

test('an allowlist given as a comma string behaves like the array', () => {
  const p = P({ allowedAgents: 'pm-elysian, build' });
  assert.equal(canPrincipalSeeAgent(p, { id: 'build' }), true);
  assert.equal(canPrincipalSeeAgent(p, { id: 'gm' }), false);
});

test('an empty allowlist legitimately sees nothing', () => {
  assert.equal(canPrincipalSeeAgent(P({ allowedAgents: [] }), { id: 'gm' }), false);
});

test('a client scope sees exactly the agents tagged client:<scope>', () => {
  const p = P({ clientScope: 'elysian', allowedAgents: 'elysian' });
  assert.equal(canPrincipalSeeAgent(p, { id: 'pm-elysian', tags: ['client:elysian'] }), true);
  assert.equal(canPrincipalSeeAgent(p, { id: 'gm', tags: ['core'] }), false);
  assert.equal(canPrincipalSeeAgent(p, { id: 'x' }), false, 'no tags is out of scope, not in');
});

test('a client scope wins over an allowlist, exactly as GET / orders them', () => {
  // GET / checks clientScope first; an id-match must not leak past a client scope.
  const p = P({ clientScope: 'elysian', allowedAgents: ['gm'] });
  assert.equal(canPrincipalSeeAgent(p, { id: 'gm', tags: [] }), false);
});

test('tags given as a comma string are read like an array, as GET / coerces them', () => {
  assert.deepEqual(coerceTags('client:elysian, core'), ['client:elysian', 'core']);
  assert.deepEqual(coerceTags(undefined), []);
  assert.deepEqual(coerceTags(42), []);
  const p = P({ clientScope: 'elysian', allowedAgents: 'elysian' });
  assert.equal(canPrincipalSeeAgent(p, { id: 'pm', tags: 'client:elysian,core' }), true);
});

// ---- the param middleware every /:id route runs ---------------------------------------------

const TAGS: Record<string, unknown> = { 'pm-elysian': ['client:elysian'], gm: ['core'] };

function run(id: string, headers: Record<string, string>) {
  const mw = makeAgentScopeParam((i) => TAGS[i]);
  const out = { status: 0, body: undefined as unknown, nexted: false };
  const res = {
    status(n: number) { out.status = n; return this; },
    json(b: unknown) { out.body = b; return this; },
  };
  mw({ headers } as unknown as Request, res as never, () => { out.nexted = true; }, id);
  return out;
}

const trusted = <T>(fn: () => T): T => {
  const prev = process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
  process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = '1';
  try { return fn(); } finally {
    if (prev === undefined) delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
    else process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = prev;
  }
};
const untrusted = <T>(fn: () => T): T => {
  const prev = process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
  delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
  try { return fn(); } finally {
    if (prev !== undefined) process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = prev;
  }
};

const ELYSIAN = { 'x-orchestra-user': 'client1', 'x-orchestra-role': 'viewer', 'x-orchestra-client': 'elysian' };

test('trusted + scoped: an in-scope agent passes through to the handler', () => {
  const r = trusted(() => run('pm-elysian', ELYSIAN));
  assert.equal(r.nexted, true);
  assert.equal(r.status, 0);
});

test('trusted + scoped: an out-of-scope agent is refused before the handler runs', () => {
  const r = trusted(() => run('gm', ELYSIAN));
  assert.equal(r.nexted, false, 'the handler must not run — for kill/inject this is the whole point');
  assert.equal(r.status, 404);
});

test('an out-of-scope agent and an unknown one are indistinguishable', () => {
  // 404, not 403, and the same body: otherwise the route is an oracle for which ids exist.
  const outOfScope = trusted(() => run('gm', ELYSIAN));
  const unknown = trusted(() => run('no-such-agent', ELYSIAN));
  assert.equal(outOfScope.status, unknown.status);
  assert.deepEqual(
    JSON.stringify(outOfScope.body).replace('gm', 'X'),
    JSON.stringify(unknown.body).replace('no-such-agent', 'X'),
  );
});

test('the refusal body matches the route-level not-found body GET /:id already returns', () => {
  const r = trusted(() => run('gm', ELYSIAN));
  assert.deepEqual(r.body, { error: "Agent 'gm' not found" });
});

test('trusted with no identity at all is refused (fail closed)', () => {
  const r = trusted(() => run('pm-elysian', {}));
  assert.equal(r.nexted, false);
  assert.equal(r.status, 404);
});

test('UNTRUSTED (the default install) is unchanged: every id passes', () => {
  // principal() is the configured operator with '*' here. A scope fix that broke the
  // single-operator install would be worse than the gap it closes.
  for (const id of ['gm', 'pm-elysian', 'no-such-agent']) {
    const r = untrusted(() => run(id, ELYSIAN));
    assert.equal(r.nexted, true, id);
    assert.equal(r.status, 0, id);
  }
});

// ---- the GET / refactor changed nothing ------------------------------------------------------

/** GET /'s inline filter as it stood before this change, copied verbatim, as an oracle. */
function legacyGetRootFilter(p: Principal | null, all: { id: string; tags?: unknown }[]) {
  const clientScope = p?.clientScope || '';
  const allowedAgents = p?.allowedAgents ?? [];
  let visibleAgents = all;
  if (clientScope) {
    visibleAgents = all.filter(a => {
      const tags: string[] = (a as any).tags || [];
      return tags.includes(`client:${clientScope}`);
    });
  } else if (allowedAgents !== '*') {
    const allowed = new Set(
      (Array.isArray(allowedAgents) ? allowedAgents : String(allowedAgents).split(','))
        .map(s => String(s).trim()).filter(Boolean));
    visibleAgents = all.filter(a => allowed.has(a.id));
  }
  return visibleAgents.map(a => a.id);
}

test('canPrincipalSeeAgent selects exactly what GET / selected before the refactor', () => {
  // Array-shaped tags only: GET / coerces tags to arrays before filtering, so the legacy
  // filter never saw a string. (On a raw string, .includes would have done substring matching.)
  const fleet = [
    { id: 'gm', tags: ['core'] },
    { id: 'pm-elysian', tags: ['client:elysian'] },
    { id: 'pm-yura', tags: ['client:yura', 'core'] },
    { id: 'untagged' },
    { id: 'unregistered:foo', tags: [] },
  ];
  const principals: (Principal | null)[] = [
    null,
    P({ allowedAgents: '*' }),
    P({ allowedAgents: [] }),
    P({ allowedAgents: ['gm', 'untagged'] }),
    P({ allowedAgents: 'pm-yura, gm' }),
    P({ clientScope: 'elysian', allowedAgents: 'elysian' }),
    P({ clientScope: 'yura', allowedAgents: ['gm'] }),
    P({ clientScope: 'nobody', allowedAgents: '*' }),
  ];
  for (const p of principals) {
    const now = fleet.filter((a) => canPrincipalSeeAgent(p, a)).map((a) => a.id);
    assert.deepEqual(now, legacyGetRootFilter(p, fleet), JSON.stringify(p));
  }
});
