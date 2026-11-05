/**
 * /api/supervision: levels, roles, standing grants and the gate, all forwarded to the gateway with
 * the server bearer. The API's own job is scope: a member reads and writes only for agents they can
 * see; the person's default, project defaults, role edits, role/project/fleet grants and the gate
 * are an unscoped admin's. Also the answer route's option-4 check on a gate card (all agents).
 * Pure over injected deps and an in-process express app.
 * Run: npx tsx --test src/routes/supervision.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createSupervisionRouter, type SupervisionDeps } from './supervision.js';
import { gateOptionRefusal } from './unified-approvals.js';
import { canonicalAnswerArgv } from './_canonical-approvals.js';

type Call = { url: string; init: RequestInit };

const ADMIN = { username: 'operator', role: 'admin', allowedAgents: '*', clientScope: null, trusted: false };
const MEMBER_GM = { username: 'member', role: 'member', allowedAgents: ['gm'], clientScope: null, trusted: true };
const SCOPED_ADMIN = { username: 'client-admin', role: 'admin', allowedAgents: ['gm'], clientScope: null, trusted: true };

const SUP = {
  ok: true, person: 'operator', armed: true, gate_mode: 'enforce',
  default: { level: 'let_them_run', high_risk: [], hold_wait_s: 0, source: 'seed' },
  roles: [{ id: 'rol_a', name: 'Hands-off', level: 'let_them_run', used_by: { projects: 1, agents: 0 } }],
  projects: [
    { key: '/srv/projects/alpha', label: 'alpha', role_id: 'rol_a', agents: ['gm', 'pm'] },
    { key: '/srv/projects/beta', label: 'beta', role_id: 'rol_a', agents: ['pm'] },
  ],
  agents: [
    { agent: 'gm', runtime: 'claude', effective: { level: 'let_them_run' } },
    { agent: 'pm', runtime: 'claude', effective: { level: 'let_them_run' } },
  ],
};

const GRANTS = [
  { id: 'grt_gm', scope: 'agent', scope_ref: 'gm', category: 'outside_world', effect: 'allow', state: 'active' },
  { id: 'grt_pm', scope: 'agent', scope_ref: 'pm', category: 'outside_world', effect: 'allow', state: 'active' },
  { id: 'grt_ses_gm', scope: 'session', scope_ref: '11111111-1111-1111-1111-111111111111', agent: 'gm', category: 'install', effect: 'allow', state: 'active' },
  { id: 'grt_ses_x', scope: 'session', scope_ref: '22222222-2222-2222-2222-222222222222', category: 'install', effect: 'allow', state: 'active' },
  { id: 'grt_fleet', scope: 'fleet', scope_ref: null, category: 'install', effect: 'allow', state: 'active' },
  { id: 'grt_role', scope: 'role', scope_ref: 'rol_a', category: 'install', effect: 'allow', state: 'active' },
];

function makeDeps(over: Partial<SupervisionDeps> = {}, respond?: (url: string, init: RequestInit) => { status: number; body: any }) {
  const calls: Call[] = [];
  const deps: any = {
    token: () => 'gw-token',
    gatewayUrl: () => 'http://gw.test',
    principal: () => ADMIN,
    role: () => 'admin',
    resolveTags: () => [],
    agentIds: () => ['gm', 'pm'],
    fetchJson: async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      if (respond) return respond(url, init);
      const u = new URL(url);
      const m = init.method || 'GET';
      if (u.pathname === '/supervision' && m === 'GET') return { status: 200, body: SUP };
      if (u.pathname === '/grants' && m === 'GET') return { status: 200, body: { ok: true, grants: GRANTS } };
      if (u.pathname.startsWith('/gate/holds/')) return { status: 200, body: { id: 'hld_1', agent: 'pm', state: 'held' } };
      return { status: 200, body: { ok: true, echo: u.pathname } };
    },
    ...over,
  };
  deps.calls = calls;
  return deps as SupervisionDeps & { calls: Call[] };
}

const asMember = (p: any = MEMBER_GM) => ({ principal: () => p });

function app(deps: SupervisionDeps) {
  const a = express();
  a.use(express.json());
  a.use('/api/supervision', createSupervisionRouter(deps));
  return a;
}

async function req(deps: SupervisionDeps, method: string, path: string, body?: unknown) {
  const server = app(deps).listen(0);
  const port = (server.address() as any).port;
  try {
    return await new Promise<{ status: number; json: any }>((resolve, reject) => {
      const data = body === undefined ? undefined : JSON.stringify(body);
      const r = http.request({ host: '127.0.0.1', port, path, method,
        headers: data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {} }, (res) => {
        let out = '';
        res.on('data', (c) => (out += c));
        res.on('end', () => resolve({ status: res.statusCode || 0, json: JSON.parse(out || '{}') }));
      });
      r.on('error', reject);
      r.setTimeout(5000, () => r.destroy(new Error(`no response in 5s: ${method} ${path}`)));
      if (data) r.write(data);
      r.end();
    });
  } finally {
    server.close();
  }
}

const writes = (deps: { calls: Call[] }) => deps.calls.filter((c) => (c.init.method || 'GET') !== 'GET');
const bodyOf = (c: Call) => JSON.parse(String(c.init.body));
const NOT_ALLOWED = { ok: false, error: 'not_allowed' };

// ---------------------------------------------------------------- GET /

test('admin GET forwards with no agents filter', async () => {
  const deps = makeDeps();
  const r = await req(deps, 'GET', '/api/supervision?agents=pm');
  assert.equal(r.status, 200);
  assert.deepEqual(r.json, SUP);
  const u = new URL(deps.calls[0].url);
  assert.equal(u.pathname, '/supervision');
  assert.equal(u.searchParams.get('agents'), null, 'an admin sends none, and a client one is dropped');
  assert.equal((deps.calls[0].init.headers as any).Authorization, 'Bearer gw-token');
});

test('member GET forwards agents=<visible> and drops others again', async () => {
  // This gateway ignores agents= and returns everything: the API's own filter must hold.
  const deps = makeDeps(asMember());
  const r = await req(deps, 'GET', '/api/supervision?agents=pm');
  assert.equal(r.status, 200);
  assert.equal(new URL(deps.calls[0].url).searchParams.get('agents'), 'gm');
  assert.deepEqual(r.json.agents.map((a: any) => a.agent), ['gm']);
  assert.deepEqual(r.json.projects.map((p: any) => [p.key, p.agents]), [['/srv/projects/alpha', ['gm']]]);
  assert.deepEqual(r.json.default, SUP.default);
});

test('a member who sees nobody never sends an empty agents= and gets no agents', async () => {
  const deps = makeDeps(asMember({ ...MEMBER_GM, allowedAgents: [] }));
  const r = await req(deps, 'GET', '/api/supervision');
  assert.equal(r.status, 200);
  assert.ok(!deps.calls[0].url.includes('agents='));
  assert.deepEqual(r.json.agents, []);
  assert.deepEqual(r.json.projects, []);
});

test('no principal is 401 everywhere and the gateway is never called', async () => {
  const deps = makeDeps({ principal: () => null });
  for (const [m, p] of [['GET', '/api/supervision'], ['PUT', '/api/supervision/default'], ['PUT', '/api/supervision/agents/gm'],
    ['PUT', '/api/supervision/projects/x'], ['GET', '/api/supervision/roles'], ['POST', '/api/supervision/roles'],
    ['PUT', '/api/supervision/roles/rol_a'], ['DELETE', '/api/supervision/roles/rol_a'], ['POST', '/api/supervision/roles/rol_a/duplicate'],
    ['GET', '/api/supervision/grants'], ['POST', '/api/supervision/grants'], ['PATCH', '/api/supervision/grants/grt_gm'],
    ['DELETE', '/api/supervision/grants/grt_gm'], ['PUT', '/api/supervision/gate/mode'], ['GET', '/api/supervision/gate/log'],
    ['GET', '/api/supervision/gate/holds/hld_1']] as const) {
    const r = await req(deps, m, p, m === 'GET' || m === 'DELETE' ? undefined : {});
    assert.equal(r.status, 401, `${m} ${p}`);
  }
  assert.equal(deps.calls.length, 0);
});

// ---------------------------------------------------------------- agents, default, gate

test('member may set level only for a visible agent', async () => {
  const deps = makeDeps(asMember());
  const no = await req(deps, 'PUT', '/api/supervision/agents/pm', { level: 'watch_closely' });
  assert.equal(no.status, 403);
  assert.deepEqual(no.json, NOT_ALLOWED);
  assert.equal(deps.calls.length, 0);
  const yes = await req(deps, 'PUT', '/api/supervision/agents/gm', { level: 'watch_closely' });
  assert.equal(yes.status, 200);
  assert.equal(new URL(deps.calls[0].url).pathname, '/supervision/agents/gm');
  assert.equal(deps.calls[0].init.method, 'PUT');
  assert.deepEqual(bodyOf(deps.calls[0]), { level: 'watch_closely' });
});

test('member cannot PUT default or gate mode, nor read the gate log', async () => {
  for (const p of [MEMBER_GM, SCOPED_ADMIN]) {
    const deps = makeDeps(asMember(p));
    for (const [m, path, b] of [['PUT', '/api/supervision/default', { level: 'check_in' }],
      ['PUT', '/api/supervision/gate/mode', { mode: 'off' }], ['GET', '/api/supervision/gate/log']] as const) {
      const r = await req(deps, m, path, b);
      assert.equal(r.status, 403, `${p.username} ${m} ${path}`);
      assert.deepEqual(r.json, NOT_ALLOWED);
    }
    assert.equal(deps.calls.length, 0);
  }
});

test('admin writes the default and gate mode; the gate log forwards limit and agent only', async () => {
  const deps = makeDeps();
  assert.equal((await req(deps, 'PUT', '/api/supervision/default', { level: 'check_in' })).status, 200);
  assert.equal((await req(deps, 'PUT', '/api/supervision/gate/mode', { mode: 'observe' })).status, 200);
  assert.equal((await req(deps, 'GET', '/api/supervision/gate/log?limit=50&agent=gm&evil=1')).status, 200);
  assert.deepEqual(deps.calls.map((c) => [c.init.method, new URL(c.url).pathname]),
    [['PUT', '/supervision/default'], ['PUT', '/gate/mode'], ['GET', '/gate/log']]);
  assert.deepEqual(bodyOf(deps.calls[1]), { mode: 'observe' });
  const log = new URL(deps.calls[2].url);
  assert.equal(log.searchParams.get('limit'), '50');
  assert.equal(log.searchParams.get('agent'), 'gm');
  assert.equal(log.searchParams.get('evil'), null);
});

test('a gate hold is readable by a member only for an agent they can see', async () => {
  const member = await req(makeDeps(asMember()), 'GET', '/api/supervision/gate/holds/hld_1');
  assert.equal(member.status, 403);
  const admin = await req(makeDeps(), 'GET', '/api/supervision/gate/holds/hld_1');
  assert.equal(admin.status, 200);
  assert.equal(admin.json.agent, 'pm');
});

// ---------------------------------------------------------------- roles + projects

test('member may assign an existing role to a visible agent but not create/edit/delete roles or set a project default', async () => {
  const deps = makeDeps(asMember());
  const assign = await req(deps, 'PUT', '/api/supervision/agents/gm', { role_id: 'rol_a' });
  assert.equal(assign.status, 200);
  assert.deepEqual(bodyOf(deps.calls[0]), { role_id: 'rol_a' });
  const read = await req(deps, 'GET', '/api/supervision/roles');
  assert.equal(read.status, 200);
  const before = deps.calls.length;
  for (const [m, p, b] of [['POST', '/api/supervision/roles', { name: 'Mine' }], ['PUT', '/api/supervision/roles/rol_a', { level: 'check_in' }],
    ['DELETE', '/api/supervision/roles/rol_a'], ['POST', '/api/supervision/roles/rol_a/duplicate', { name: 'Copy' }],
    ['PUT', `/api/supervision/projects/${encodeURIComponent('/srv/projects/alpha')}`, { role_id: 'rol_a' }]] as const) {
    const r = await req(deps, m, p, b);
    assert.equal(r.status, 403, `${m} ${p}`);
    assert.deepEqual(r.json, NOT_ALLOWED);
  }
  assert.equal(deps.calls.length, before, 'no refused write reaches the gateway');
});

test('admin role writes and the project default reach the gateway on the same paths', async () => {
  const deps = makeDeps();
  await req(deps, 'POST', '/api/supervision/roles', { name: 'Mine', level: 'check_in' });
  await req(deps, 'PUT', '/api/supervision/roles/rol_a', { level: 'check_in' });
  await req(deps, 'DELETE', '/api/supervision/roles/rol_a');
  await req(deps, 'POST', '/api/supervision/roles/rol_a/duplicate', { name: 'Copy' });
  await req(deps, 'PUT', `/api/supervision/projects/${encodeURIComponent('/srv/projects/alpha')}`, { role_id: 'rol_a' });
  assert.deepEqual(deps.calls.map((c) => [c.init.method, c.url.slice('http://gw.test'.length)]), [
    ['POST', '/supervision/roles'], ['PUT', '/supervision/roles/rol_a'], ['DELETE', '/supervision/roles/rol_a'],
    ['POST', '/supervision/roles/rol_a/duplicate'], ['PUT', '/supervision/projects/%2Fsrv%2Fprojects%2Falpha'],
  ]);
  assert.deepEqual(bodyOf(deps.calls[4]), { role_id: 'rol_a' });
});

// ---------------------------------------------------------------- grants

test('member may POST agent/session grants for visible agents only; role/project/fleet -> 403', async () => {
  const deps = makeDeps(asMember());
  const okBodies = [
    { scope: 'agent', scope_ref: 'gm', category: 'outside_world', narrow: { action: 'deploy' }, effect: 'allow' },
    { scope: 'session', scope_ref: '11111111-1111-1111-1111-111111111111', agent: 'gm', category: 'install', effect: 'allow' },
  ];
  for (const b of okBodies) assert.equal((await req(deps, 'POST', '/api/supervision/grants', b)).status, 200, b.scope);
  assert.deepEqual(writes(deps).map(bodyOf), okBodies);
  const bad = [
    { scope: 'agent', scope_ref: 'pm', category: 'outside_world' },
    { scope: 'session', scope_ref: '22222222-2222-2222-2222-222222222222', category: 'install' },
    { scope: 'session', scope_ref: '22222222-2222-2222-2222-222222222222', agent: 'pm', category: 'install' },
    { scope: 'role', scope_ref: 'rol_a', category: 'install' },
    { scope: 'project', scope_ref: '/srv/projects/alpha', category: 'install' },
    { scope: 'fleet', scope_ref: null, category: 'install' },
    { category: 'install' },
  ];
  for (const b of bad) {
    const r = await req(deps, 'POST', '/api/supervision/grants', b);
    assert.equal(r.status, 403, JSON.stringify(b));
    assert.deepEqual(r.json, NOT_ALLOWED);
  }
  assert.equal(writes(deps).length, okBodies.length);
  const admin = makeDeps();
  assert.equal((await req(admin, 'POST', '/api/supervision/grants', { scope: 'fleet', category: 'install' })).status, 200);
});

test('member GET grants: own agents and sessions, plus fleet/role/project read-only; agent= must be visible', async () => {
  const deps = makeDeps(asMember());
  const r = await req(deps, 'GET', '/api/supervision/grants?state=all&scope=agent&evil=1');
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.grants.map((g: any) => g.id), ['grt_gm', 'grt_ses_gm', 'grt_fleet', 'grt_role']);
  const u = new URL(deps.calls[0].url);
  assert.equal(u.searchParams.get('state'), 'all');
  assert.equal(u.searchParams.get('scope'), 'agent');
  assert.equal(u.searchParams.get('evil'), null);
  const other = await req(deps, 'GET', '/api/supervision/grants?agent=pm');
  assert.equal(other.status, 403);
  const admin = await req(makeDeps(), 'GET', '/api/supervision/grants');
  assert.equal(admin.json.grants.length, GRANTS.length);
});

test('PATCH/DELETE check the grant scope fetched from the gateway first', async () => {
  const deps = makeDeps(asMember());
  // in scope: lookup then the write
  const patch = await req(deps, 'PATCH', '/api/supervision/grants/grt_gm', { narrow: { folder: '/srv/projects/alpha/site' } });
  assert.equal(patch.status, 200);
  assert.deepEqual(deps.calls.map((c) => [c.init.method, c.url.slice('http://gw.test'.length)]),
    [['GET', '/grants?state=all'], ['PATCH', '/grants/grt_gm']]);
  assert.deepEqual(bodyOf(deps.calls[1]), { narrow: { folder: '/srv/projects/alpha/site' } });
  // the body cannot move a grant into scope: the stored scope decides
  for (const id of ['grt_pm', 'grt_fleet', 'grt_role', 'grt_ses_x']) {
    for (const m of ['PATCH', 'DELETE']) {
      const r = await req(deps, m, `/api/supervision/grants/${id}`, m === 'PATCH' ? { scope: 'agent', scope_ref: 'gm', narrow: {} } : undefined);
      assert.equal(r.status, 403, `${m} ${id}`);
      assert.deepEqual(r.json, NOT_ALLOWED);
    }
  }
  const unknown = await req(deps, 'DELETE', '/api/supervision/grants/grt_nope');
  assert.equal(unknown.status, 404);
  assert.equal(writes(deps).length, 1, 'only the in-scope PATCH was written');
  const del = makeDeps(asMember());
  assert.equal((await req(del, 'DELETE', '/api/supervision/grants/grt_ses_gm')).status, 200);
  assert.equal(writes(del)[0].url, 'http://gw.test/grants/grt_ses_gm');
  // an admin is not looked up
  const admin = makeDeps();
  assert.equal((await req(admin, 'DELETE', '/api/supervision/grants/grt_fleet')).status, 200);
  assert.equal(admin.calls.length, 1);
});

// ---------------------------------------------------------------- gateway errors

test('gateway 4xx bodies pass through; gateway down -> 502', async () => {
  const refused = makeDeps({}, () => ({ status: 422, body: { ok: false, error: 'invalid', field: 'level' } }));
  const r = await req(refused, 'PUT', '/api/supervision/default', { level: 'nope' });
  assert.equal(r.status, 422);
  assert.deepEqual(r.json, { ok: false, error: 'invalid', field: 'level' });
  const unarmed = makeDeps(asMember(), () => ({ status: 409, body: { ok: false, error: 'not_armed' } }));
  const u = await req(unarmed, 'PUT', '/api/supervision/agents/gm', { level: 'check_in' });
  assert.equal(u.status, 409);
  assert.deepEqual(u.json, { ok: false, error: 'not_armed' });
  const down = makeDeps({ fetchJson: async () => { throw new Error('ECONNREFUSED'); } });
  const d = await req(down, 'GET', '/api/supervision');
  assert.equal(d.status, 502);
  assert.deepEqual(d.json, { ok: false, error: 'gateway unreachable' });
  // a member's grant lookup failing is the gateway's error, and nothing is written
  const lookupDown = makeDeps({ ...asMember(), fetchJson: async () => { throw new Error('ECONNREFUSED'); } });
  const l = await req(lookupDown, 'DELETE', '/api/supervision/grants/grt_gm');
  assert.equal(l.status, 502);
  const noToken = makeDeps({ token: () => '' });
  assert.equal((await req(noToken, 'GET', '/api/supervision')).status, 502);
  assert.equal(noToken.calls.length, 0);
});

// ---------------------------------------------------------------- option 4 on a gate card

test('option 4 on a gate card needs an unscoped admin', () => {
  const gate = { menu: { bridge: { kind: 'gate' }, options: [] } } as any;
  const plain = { menu: { bridge: { kind: 'menu' }, options: [] } } as any;
  const refusal = { error: 'all_agents_needs_admin' };
  assert.deepEqual(gateOptionRefusal(gate, '4', MEMBER_GM as any), refusal);
  assert.deepEqual(gateOptionRefusal(gate, ' 4 ', SCOPED_ADMIN as any), refusal);
  assert.deepEqual(gateOptionRefusal(gate, '4', null), refusal);
  assert.equal(gateOptionRefusal(gate, '4', ADMIN as any, 'admin'), null);
  assert.deepEqual(gateOptionRefusal(gate, '4', ADMIN as any, 'member'), refusal,
    'an unscoped principal is not enough: without a proxy every caller is one; the install role decides');
  assert.deepEqual(gateOptionRefusal(gate, '4', ADMIN as any), refusal, 'no install role given = member');
  assert.equal(gateOptionRefusal(gate, '3', MEMBER_GM as any), null, 'only option 4 is the all-agents grant');
  assert.equal(gateOptionRefusal(plain, '4', MEMBER_GM as any), null, 'option 4 on another card is an ordinary pick');
  assert.equal(gateOptionRefusal(undefined, '4', MEMBER_GM as any), null);
  // a grant proposal: session / agent scope is narrow; fleet, role, project, and a missing or unknown scope are wide
  const grant = (scope?: string) => ({ menu: { bridge: { kind: 'gate_grant', ...(scope ? { scope } : {}) }, options: [] } }) as any;
  for (const s of ['session', 'agent']) assert.equal(gateOptionRefusal(grant(s), '1', MEMBER_GM as any), null, s);
  for (const s of ['fleet', 'role', 'project', undefined, 'galaxy']) {
    assert.deepEqual(gateOptionRefusal(grant(s), '1', MEMBER_GM as any), refusal, String(s));
  }
  assert.equal(gateOptionRefusal(grant(), '1', ADMIN as any, 'admin'), null);
  // an admin's pick reaches approval.py as --option-n 4
  const argv = canonicalAnswerArgv('apr_1', 'option', undefined, { optionN: '4' });
  assert.equal(argv[argv.indexOf('--option-n') + 1], '4');
});

test('the answer route refuses with 403 before anything is answered', () => {
  const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'unified-approvals.ts'), 'utf-8');
  const handler = src.slice(src.indexOf("router.post('/:id/answer'"));
  const check = handler.indexOf('gateOptionRefusal(');
  const answer = handler.indexOf('canonicalAnswer(');
  assert.ok(check > 0 && answer > 0 && check < answer, 'the option-4 check runs before canonicalAnswer');
  assert.match(handler.slice(check - 200, answer), /res\.status\(403\)\.json\(refusal\)/);
  // the cards are read fail-closed: an unreadable list is a 503, never "no such card, go ahead"
  assert.ok(handler.slice(0, check).includes('readCanonicalPendingOrNull()'), 'the check reads the strict list');
  assert.match(handler.slice(0, check), /if \(!rows\) \{ res\.status\(503\)/);
});

test('admin-only = unscoped principal AND install admin: an install member is refused (no proxy: every principal is unscoped)', async () => {
  const deps = makeDeps({ role: () => 'member' });
  const list: Array<[string, string, any]> = [
    ['PUT', '/api/supervision/default', { level: 'watch_closely' }],
    ['PUT', '/api/supervision/projects/' + encodeURIComponent('/srv/projects/alpha'), { role_id: 'rol_a' }],
    ['POST', '/api/supervision/roles', { name: 'x', level: 'check_in' }],
    ['PUT', '/api/supervision/roles/rol_a', { name: 'y' }],
    ['DELETE', '/api/supervision/roles/rol_a', undefined],
    ['POST', '/api/supervision/roles/rol_a/duplicate', {}],
    ['PUT', '/api/supervision/gate/mode', { mode: 'off' }],
    ['GET', '/api/supervision/gate/log', undefined],
    ['POST', '/api/supervision/grants', { scope: 'fleet', category: 'install' }],
    ['PATCH', '/api/supervision/grants/grt_fleet', { narrow: { action: 'npm' } }],
    ['DELETE', '/api/supervision/grants/grt_role', undefined],
  ];
  for (const [m, path, body] of list) {
    const r = await req(deps, m, path, body);
    assert.equal(r.status, 403, `${m} ${path}`);
  }
  assert.equal(writes(deps).length, 0, 'no write reached the gateway');
  // an agent the member sees is still theirs to set, and an agent grant on it is allowed
  assert.equal((await req(deps, 'PUT', '/api/supervision/agents/gm', { level: 'check_in' })).status, 200);
  assert.equal((await req(deps, 'POST', '/api/supervision/grants', { scope: 'agent', scope_ref: 'gm', category: 'install' })).status, 200);
});

test('Confirm on a fleet/role/project grant proposal needs an admin; an agent-scope proposal does not', () => {
  const card = (scope: string) => ({ menu: { bridge: { kind: 'gate_grant', grant_id: 'grt_1', scope }, options: [] } }) as any;
  const refusal = { error: 'all_agents_needs_admin' };
  for (const scope of ['fleet', 'role', 'project']) {
    assert.deepEqual(gateOptionRefusal(card(scope), '1', MEMBER_GM as any, 'member'), refusal, scope);
    assert.deepEqual(gateOptionRefusal(card(scope), '1', ADMIN as any, 'member'), refusal, scope);
    assert.equal(gateOptionRefusal(card(scope), '1', ADMIN as any, 'admin'), null, scope);
    assert.equal(gateOptionRefusal(card(scope), '2', MEMBER_GM as any, 'member'), null, "Don't is anyone's");
  }
  assert.equal(gateOptionRefusal(card('agent'), '1', MEMBER_GM as any, 'member'), null);
  assert.equal(gateOptionRefusal(card('session'), '1', MEMBER_GM as any, 'member'), null);
});
