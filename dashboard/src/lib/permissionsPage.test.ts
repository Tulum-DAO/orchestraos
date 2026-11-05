import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseSupervision, type AgentSupervision, type Category, type Grant, type GrantScope, type Role, type Supervision } from './supervision.ts';
import {
  grantsByLevel, grantLabel, narrowOptions, validateNewGrant, roleSummary, validateRole, projectRows, joinPhrases, usedByLine, newGrantBody,
  grantSource, scopeNames,
} from './permissionsPage.ts';

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


test('roles read as one line and validate', () => {
  assert.equal(roleSummary(ROLE_TRUSTED, CATS), 'Check in · asks before destructive and outside-world actions · may install npm and Python packages');
  assert.equal(roleSummary(ROLE_HANDS_OFF, CATS), 'Let them run · nothing waits for you');
  assert.equal(roleSummary(ROLE_SUPERVISED, CATS), 'Watch closely · asks before anything risky');
  assert.equal(validateRole({ name: '' }), 'Give the role a name');
  assert.equal(validateRole({ name: '  ' }), 'Give the role a name');
  assert.equal(validateRole({ name: 'Hands-off' }, ['Hands-off']), 'A role with that name exists');
  assert.equal(validateRole({ name: 'hands-off ' }, ['Hands-off']), 'A role with that name exists');
  assert.equal(validateRole({ name: 'Reviewer' }, ['Hands-off']), null);
  assert.equal(usedByLine({ used_by: { projects: 3, agents: 1 } }), 'used by 3 projects · 1 agent');
  assert.equal(usedByLine({ used_by: { projects: 0, agents: 0 } }), 'not used');
});

test('phrases that share words say them once', () => {
  assert.equal(joinPhrases(['deploy']), 'deploy');
  assert.equal(joinPhrases(['deploy', 'push code']), 'deploy and push code');
  assert.equal(joinPhrases(['install npm packages', 'install Python packages', 'install system packages']), 'install npm, Python and system packages');
});

test('project rows show the default role and its agents', () => {
  assert.deepEqual(projectRows(SUP).map((p) => [p.label, p.roleName]), [['website', 'Hands-off']]);
  assert.deepEqual(projectRows(SUP)[0].agents, ['dev']);
});

test('grants group by level in Settings order: whole server, projects, roles, agents, sessions', () => {
  const g = grantsByLevel([G('session'), G('fleet'), G('agent'), G('project'), G('role')]);
  assert.deepEqual(g.map((x) => x.title), ['Whole server', 'Projects', 'Roles', 'Agents', 'Sessions']);
  assert.deepEqual(grantsByLevel([G('agent')]).map((x) => x.title), ['Agents'], 'a level with no grants is left out');
});

test('grant label reads as a sentence', () => {
  assert.equal(grantLabel(G('agent', { ref: 'dev', category: 'outside_world', action: 'deploy' }), CATS), 'dev may deploy without asking');
  assert.equal(grantLabel(G('fleet', { category: 'install', action: 'npm' }), CATS), 'All agents may install npm packages without asking');
  assert.equal(grantLabel(G('role', { ref: 'rT', category: 'install', action: 'pip' }), CATS, { rT: 'Trusted builder' }), 'Trusted builder agents may install Python packages without asking');
  assert.equal(grantLabel(G('project', { ref: '/p/website', category: 'install' }), CATS, scopeNames(SUP)), 'Agents in website may install packages without asking');
  assert.equal(grantLabel(G('agent', { ref: 'dev', category: 'outside_world', action: 'deploy', effect: 'deny' }), CATS), 'dev may not deploy without asking');
  const folder = { ...G('agent', { ref: 'dev', category: 'outside_folder', action: 'edit_outside' }), narrow: { action: 'edit_outside', folder: '/srv/site' } };
  assert.equal(grantLabel(folder, CATS), 'dev may edit files outside its folder in /srv/site without asking');
});

test('a grant row says where it came from', () => {
  const now = new Date('2026-11-06T12:00:00Z');
  assert.equal(grantSource({ ...G('agent'), source: 'card', created_at: '2026-11-05T10:04:00Z' }, now), 'from a permission card · 5 Nov');
  assert.equal(grantSource({ ...G('agent'), source: 'chat', said: 'you can deploy', created_at: null }, now), "you said in chat: 'you can deploy'");
  assert.equal(grantSource({ ...G('role'), source: 'starter' }, now), 'starter role');
});

test('narrowing offers only what the category allows', () => {
  assert.deepEqual(narrowOptions('outside_folder', CATS).map((o) => o.kind), ['action', 'folder']);
  assert.deepEqual(narrowOptions('destructive', CATS).map((o) => o.kind), ['action', 'folder']);
  assert.deepEqual(narrowOptions('outside_world', CATS).map((o) => o.kind), ['action', 'recipient']);
  assert.deepEqual(narrowOptions('install', CATS).map((o) => o.kind), ['action']);
  const first = narrowOptions('install', CATS)[0];
  assert.ok(first.kind === 'action' && first.actions.map((a) => a.id).join() === 'npm,pip');
});

test('new grant validation', () => {
  assert.equal(validateNewGrant({ scope: 'agent', ref: '', category: 'install' }), 'Pick an agent');
  assert.equal(validateNewGrant({ scope: 'role', ref: '', category: 'install' }), 'Pick a role');
  assert.equal(validateNewGrant({ scope: 'project', category: 'install' }), 'Pick a project');
  assert.equal(validateNewGrant({ scope: 'fleet', category: '' }), 'Pick what they may do');
  assert.equal(validateNewGrant({ scope: 'fleet', category: 'install', folder: 'rel/path' }), 'Use a full folder path');
  assert.equal(validateNewGrant({ scope: 'fleet', category: 'install', folder: '/a/../b' }), 'Use a full folder path');
  assert.equal(validateNewGrant({ scope: 'fleet', category: 'install' }), null);
  assert.deepEqual(newGrantBody({ scope: 'fleet', ref: 'ignored', category: 'install', action: 'npm', folder: ' ' }),
    { scope: 'fleet', scope_ref: null, category: 'install', narrow: { action: 'npm' }, effect: 'allow' });
  assert.deepEqual(newGrantBody({ scope: 'agent', ref: 'dev', category: 'outside_world', recipient: 'ops@example.com' }),
    { scope: 'agent', scope_ref: 'dev', category: 'outside_world', narrow: { recipient: 'ops@example.com' }, effect: 'allow' });
});
