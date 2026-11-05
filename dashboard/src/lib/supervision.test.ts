import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  parseSupervision, parseGrants, levelLabel, gateBadge, waitLabel, fetchSupervision, putAgent, createGrant, revokeGrant, errorText,
  type AgentSupervision, type Category, type Grant, type GrantScope, type Role, type Supervision,
} from './supervision.ts';

const CATS: Category[] = [
  { id: 'outside_world', label: 'Outside-world actions', actions: [{ id: 'deploy', label: 'Deploys' }, { id: 'push', label: 'Pushing code' }] },
  { id: 'destructive', label: 'Destructive actions', actions: [{ id: 'delete_files', label: 'Deleting files' }] },
  { id: 'outside_folder', label: 'Work outside its folder', actions: [{ id: 'edit_outside', label: 'Editing files outside its folder' }] },
  { id: 'install', label: 'Installing software', actions: [{ id: 'npm', label: 'npm / node packages' }, { id: 'pip', label: 'Python packages' }] },
];
function G(scope: GrantScope, o: { id?: string; ref?: string; category?: string; action?: string; effect?: 'allow' | 'deny' } = {}): Grant {
  return {
    id: o.id ?? `g_${scope}`, scope, scope_ref: scope === 'fleet' ? null : o.ref ?? 'x', category: o.category ?? 'install',
    narrow: o.action ? { action: o.action } : {}, effect: o.effect ?? 'allow', state: 'active', source: 'settings',
  };
}
const ROLE_HANDS_OFF: Role = { id: 'rH', name: 'Hands-off', level: 'let_them_run', high_risk: [], hold_wait_s: 0, starter: 'hands_off', used_by: { projects: 1, agents: 0 } };
const ROLE_TRUSTED: Role = {
  id: 'rT', name: 'Trusted builder', level: 'check_in', high_risk: ['destructive', 'outside_world'], hold_wait_s: 0, starter: 'trusted_builder',
  used_by: { projects: 0, agents: 0 }, grants: [G('role', { ref: 'rT', action: 'npm' }), G('role', { ref: 'rT', action: 'pip' })],
};
const ROLE_SUPERVISED: Role = { id: 'rS', name: 'Supervised', level: 'watch_closely', high_risk: [], hold_wait_s: 0, starter: 'supervised', used_by: { projects: 0, agents: 0 } };
const AGENT_DEV: AgentSupervision = {
  agent: 'dev', runtime: 'claude', project: '/p/website', role: { id: 'rH', name: 'Hands-off', source: 'project' },
  override: { level: null, high_risk: null, hold_wait_s: null }, effective: { level: 'let_them_run', high_risk: [], hold_wait_s: 0 },
  gate: { status: 'protected' },
};
const AGENT_CODEX: AgentSupervision = {
  agent: 'cx', runtime: 'codex', role: null, override: { level: 'watch_closely' },
  effective: { level: 'watch_closely', high_risk: [], hold_wait_s: 0 }, gate: { status: 'not_supported', label: "Can't be gated yet" },
};
const RAW = {
  ok: true, person: 'operator', armed: true, gate_mode: 'enforce',
  default: { level: 'let_them_run', high_risk: [], hold_wait_s: 0, source: 'seed' },
  levels: [{ id: 'watch_closely', label: 'Watch closely', detail: 'a' }, { id: 'check_in', label: 'Check in', detail: 'b' }, { id: 'let_them_run', label: 'Let them run', detail: 'c' }],
  wait_choices: [0, 60, 120, 300, 540],
  categories: CATS,
  roles: [ROLE_HANDS_OFF, ROLE_TRUSTED, ROLE_SUPERVISED],
  projects: [{ key: '/p/website', label: 'website', role_id: 'rH', agents: ['dev'] }],
  agents: [AGENT_DEV, AGENT_CODEX],
};
const parsed = parseSupervision(RAW);
if (!parsed.ok) throw new Error('fixture');
const SUP: Supervision = parsed.value;


test('levels, waits and gate badges', () => {
  assert.equal(levelLabel('watch_closely'), 'Watch closely');
  assert.equal(levelLabel('check_in', [{ id: 'check_in', label: 'Check in (server)', detail: '' }]), 'Check in (server)', 'the server label wins');
  assert.equal(waitLabel(0), "Don't wait (agent carries on)");
  assert.equal(waitLabel(60), 'Wait up to 1 min');
  assert.equal(waitLabel(300), 'Wait up to 5 min');
  assert.equal(waitLabel(540), 'Wait up to 9 min');
  assert.equal(gateBadge({ status: 'not_supported' }), "Can't be gated yet");
  assert.equal(gateBadge({ status: 'not_seen' }), 'Not protected yet');
  assert.equal(gateBadge({ status: 'protected' }), 'Protected');
  assert.equal(gateBadge({ status: 'observing' }), 'Observing only');
  assert.equal(gateBadge(null), 'Not protected yet');
});

test('parseSupervision reads the GET /supervision body and refuses anything else', () => {
  assert.equal(SUP.gate_mode, 'enforce');
  assert.equal(SUP.armed, true);
  assert.deepEqual(SUP.roles.map((r) => r.name), ['Hands-off', 'Trusted builder', 'Supervised']);
  assert.equal(SUP.agents[1].gate.status, 'not_supported');
  assert.equal(SUP.agents[1].project, null);
  assert.deepEqual(SUP.wait_choices, [0, 60, 120, 300, 540]);
  assert.equal(SUP.roles[1].grants?.length, 2);
  const bad = parseSupervision({ ok: false, error: 'not_allowed' });
  assert.deepEqual(bad, { ok: false, error: 'not_allowed' });
  assert.equal(parseSupervision(null).ok, false);
  const odd = parseSupervision({ ...RAW, gate_mode: 'weird', levels: [], wait_choices: 'x' });
  assert.ok(odd.ok);
  if (odd.ok) {
    assert.equal(odd.value.gate_mode, 'off', 'an unknown mode reads as off');
    assert.deepEqual(odd.value.levels.map((l) => l.id), ['watch_closely', 'check_in', 'let_them_run']);
    assert.deepEqual(odd.value.wait_choices, [0, 60, 120, 300, 540]);
  }
});

test('parseGrants keeps well-formed grants and defaults the narrowing and effect', () => {
  const r = parseGrants({ ok: true, grants: [{ id: 'g', scope: 'fleet', scope_ref: null, category: 'install', state: 'active' }, { nope: 1 }] });
  assert.ok(r.ok);
  if (r.ok) assert.deepEqual(r.value.map((g) => [g.id, g.effect, g.narrow]), [['g', 'allow', {}]]);
});

function fakeFetch(status: number, body: unknown, seen: { url?: string; init?: RequestInit }[] = []): typeof fetch {
  return (async (url: string, init?: RequestInit) => {
    seen.push({ url, init });
    return { ok: status < 400, status, json: async () => body } as Response;
  }) as unknown as typeof fetch;
}

test('requests: paths, methods and bodies; server error codes become words', async () => {
  const seen: { url?: string; init?: RequestInit }[] = [];
  const got = await fetchSupervision(fakeFetch(200, RAW, seen));
  assert.ok(got.ok);
  assert.equal(seen[0].url, '/api/supervision');
  await putAgent('dev x', { role_id: null }, fakeFetch(200, RAW, seen));
  assert.equal(seen[1].url, '/api/supervision/agents/dev%20x');
  assert.equal(seen[1].init?.method, 'PUT');
  assert.deepEqual(JSON.parse(String(seen[1].init?.body)), { role_id: null });
  const made = await createGrant({ scope: 'agent', scope_ref: 'dev', category: 'install', narrow: {}, effect: 'allow' },
    fakeFetch(201, { ok: true, grant: { id: 'g1', scope: 'agent', scope_ref: 'dev', category: 'install', narrow: {}, effect: 'allow', state: 'active' } }, seen));
  assert.ok(made.ok && made.value.id === 'g1');
  assert.equal(seen[2].url, '/api/supervision/grants');
  const refused = await revokeGrant('g1', fakeFetch(409, { ok: false, error: 'not_armed' }, seen));
  assert.deepEqual(refused, { ok: false, error: errorText('not_armed'), status: 409 });
  assert.equal(seen[3].init?.method, 'DELETE');
  const down = await fetchSupervision((async () => { throw new Error('offline'); }) as unknown as typeof fetch);
  assert.deepEqual(down, { ok: false, error: 'offline' });
});
