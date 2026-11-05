/**
 * The agent drawer's Supervision page helpers (components/arturo/SupervisionPage.tsx): the Supervision row's value,
 * the native role dropdown's options, and what a grant's switch does for this one agent.
 */
import { gateBadge, levelLabel, type AgentSupervision, type Grant, type Level, type NewGrantBody, type Supervision } from './supervision.ts';

/** The role dropdown: the agent's project default first (value ''), then every role. */
export function roleOptions(s: Pick<Supervision, 'roles' | 'projects' | 'agents' | 'default' | 'levels'>, agentId: string): { value: string; label: string }[] {
  const a = s.agents.find((x) => x.agent === agentId);
  const project = a?.project ? s.projects.find((p) => p.key === a.project) : s.projects.find((p) => p.agents.includes(agentId));
  const projectRole = project ? s.roles.find((r) => r.id === project.role_id)?.name : undefined;
  const first = projectRole ? `Project default (${projectRole})` : `Your default (${levelLabel(s.default.level, s.levels)})`;
  return [{ value: '', label: first }, ...s.roles.map((r) => ({ value: r.id, label: r.name }))];
}

/** The dropdown's value: the agent's own role, or '' while it follows its project. */
export function roleValue(a: AgentSupervision): string {
  return a.role && a.role.source === 'agent' ? a.role.id : '';
}

/** Whether any part of the agent's policy is its own (an override). */
export function hasOverride(a: AgentSupervision): boolean {
  const o = a.override;
  return o.level != null || o.high_risk != null || o.hold_wait_s != null;
}

/** The Supervision row's value: "Hands-off (project default) · Let them run"; "Can't be gated yet" for a runtime the
 *  gate cannot hold (codex, grok). */
export function panelValue(a: AgentSupervision | null | undefined, levels?: readonly Level[]): string {
  if (!a) return '';
  if (a.gate.status === 'not_supported') return gateBadge(a.gate);
  const role = a.role ? `${a.role.name}${a.role.source === 'project' ? ' (project default)' : ''}` : 'Your default';
  const level = levelLabel(a.effective.level, levels);
  return `${role} · ${level}${hasOverride(a) ? ' (own setting)' : ''}`;
}

const sameNarrow = (a: Grant['narrow'], b: Grant['narrow']) =>
  (a?.action ?? null) === (b?.action ?? null) && (a?.folder ?? null) === (b?.folder ?? null) && (a?.recipient ?? null) === (b?.recipient ?? null);

/** This agent's own deny that switches an inherited grant off. */
export function denyFor(g: Grant, agentId: string, grants: readonly Grant[]): Grant | undefined {
  return grants.find((d) => d.effect === 'deny' && d.scope === 'agent' && d.scope_ref === agentId && d.state === 'active'
    && d.category === g.category && sameNarrow(d.narrow, g.narrow));
}

export type GrantToggle = { op: 'create'; body: NewGrantBody } | { op: 'revoke'; id: string } | { op: 'none' };
/** A grant's switch in the drawer. Off: the agent's or session's own grant is revoked; an inherited role, project or
 *  whole-server grant is overridden by an agent-level deny (never revoked: it is other agents' too). On: that deny is
 *  deleted. */
export function toggleGrant(g: Grant, agentId: string, on: boolean, grants: readonly Grant[] = []): GrantToggle {
  if (!on) {
    if (g.scope === 'agent' || g.scope === 'session') return { op: 'revoke', id: g.id };
    return { op: 'create', body: { scope: 'agent', scope_ref: agentId, category: g.category, narrow: { ...g.narrow }, effect: 'deny' } };
  }
  const deny = denyFor(g, agentId, grants);
  return deny ? { op: 'revoke', id: deny.id } : { op: 'none' };
}

/** "What <name> may do without asking": every active allow grant that reaches the agent, switched on unless the
 *  agent's own deny overrides it. The denies themselves are the switches' off state. */
export function drawerGrants(grants: readonly Grant[], agentId: string): { grant: Grant; on: boolean }[] {
  return grants.filter((g) => g.effect === 'allow' && g.state === 'active')
    .map((g) => ({ grant: g, on: !denyFor(g, agentId, grants) }));
}
