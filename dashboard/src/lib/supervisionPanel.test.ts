import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseSupervision, type AgentSupervision, type Category, type Grant, type GrantScope, type Role, type Supervision } from './supervision.ts';
import { panelValue, roleOptions, toggleGrant, roleValue, drawerGrants } from './supervisionPanel.ts';

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


test('drawer: role dropdown options and value line', () => {
  assert.deepEqual(roleOptions(SUP, 'dev').map((o) => o.label), ['Project default (Hands-off)', 'Hands-off', 'Trusted builder', 'Supervised']);
  assert.equal(roleOptions(SUP, 'dev')[0].value, '', 'the project default is no role of its own');
  assert.equal(roleOptions(SUP, 'cx')[0].label, 'Your default (Let them run)', 'an agent with no project follows the person');
  assert.equal(panelValue(AGENT_DEV), 'Hands-off (project default) · Let them run');
  assert.equal(panelValue({ ...AGENT_DEV, role: { id: 'rT', name: 'Trusted builder', source: 'agent' }, effective: { level: 'check_in', high_risk: [], hold_wait_s: 0 } }),
    'Trusted builder · Check in');
  assert.equal(panelValue({ ...AGENT_DEV, override: { level: 'watch_closely' }, effective: { level: 'watch_closely', high_risk: [], hold_wait_s: 0 } }),
    'Hands-off (project default) · Watch closely (own setting)');
  assert.equal(panelValue(AGENT_CODEX), "Can't be gated yet");
  assert.equal(roleValue(AGENT_DEV), '');
  assert.equal(roleValue({ ...AGENT_DEV, role: { id: 'rT', name: 'Trusted builder', source: 'agent' } }), 'rT');
});

test('drawer toggles', () => {
  // turning off an inherited fleet or role grant for one agent creates an agent-level deny, never revokes the broader grant
  assert.deepEqual(toggleGrant(G('role', { id: 'g1', ref: 'rT', category: 'install', action: 'npm' }), 'dev', false),
    { op: 'create', body: { scope: 'agent', scope_ref: 'dev', category: 'install', narrow: { action: 'npm' }, effect: 'deny' } });
  assert.deepEqual(toggleGrant(G('fleet', { id: 'g3', category: 'install', action: 'npm' }), 'dev', false).op, 'create');
  assert.deepEqual(toggleGrant(G('agent', { id: 'g2', ref: 'dev' }), 'dev', false), { op: 'revoke', id: 'g2' });
  assert.deepEqual(toggleGrant(G('session', { id: 'g4', ref: 'sess' }), 'dev', false), { op: 'revoke', id: 'g4' });
  // switching it back on deletes that deny
  const role = G('role', { id: 'g1', ref: 'rT', category: 'install', action: 'npm' });
  const deny = G('agent', { id: 'd1', ref: 'dev', category: 'install', action: 'npm', effect: 'deny' });
  const otherDeny = G('agent', { id: 'd2', ref: 'dev', category: 'install', action: 'pip', effect: 'deny' });
  assert.deepEqual(toggleGrant(role, 'dev', true, [role, otherDeny, deny]), { op: 'revoke', id: 'd1' });
  assert.deepEqual(toggleGrant(role, 'dev', true, [role]), { op: 'none' });
  assert.deepEqual(drawerGrants([role, deny, G('fleet', { id: 'g5', action: 'pip' })], 'dev').map((x) => [x.grant.id, x.on]), [['g1', false], ['g5', true]]);
});
