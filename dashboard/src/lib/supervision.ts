/**
 * supervision.ts — the supervision client (api routes/supervision.ts → the gateway's /supervision, /grants, /gate/*):
 * how closely each agent is watched (a level, from its own override, its role, its project's role or the person's
 * default), the roles, the standing grants ("may do X without asking") and the safety gate's mode. Every fetch goes
 * through a JSON shape guard in the style of lib/settingsApi.ts, and every label comes from the server's `levels`,
 * `categories` and `roles`; the words here are the fallbacks and the sentence glue.
 */
import type { AvailableAction, TimeoutRule } from './rulesPage';

export type LevelId = 'watch_closely' | 'check_in' | 'let_them_run';
export type GateMode = 'off' | 'observe' | 'enforce';
export type GrantScope = 'session' | 'agent' | 'role' | 'project' | 'fleet';

export interface Level { id: LevelId; label: string; detail: string }
export interface CategoryAction { id: string; label: string }
export interface Category { id: string; label: string; detail?: string; actions: CategoryAction[] }
export interface Narrow { action?: string | null; folder?: string | null; recipient?: string | null }
export interface Grant {
  id: string; scope: GrantScope; scope_ref: string | null; category: string; narrow: Narrow;
  effect: 'allow' | 'deny'; state: 'active' | 'pending' | 'revoked' | 'declined' | string; source?: string;
  source_card?: string | null; said?: string | null; proposed_by?: string | null;
  created_at?: string | null; created_by?: string | null; label?: string | null;
  applies?: boolean; overridden_by?: string | null;
}
export interface Role {
  id: string; name: string; description?: string | null; level: LevelId; high_risk: string[]; hold_wait_s: number;
  starter?: string | null; used_by: { projects: number; agents: number }; grants?: Grant[];
}
export interface ProjectInfo { key: string; label: string; role_id: string | null; agents: string[] }
export interface GateInfo { status: string; label?: string; seen_at?: string | null; session_id?: string | null }
export interface Policy { level: LevelId; high_risk: string[]; hold_wait_s: number }
export interface Override { level?: LevelId | null; high_risk?: string[] | null; hold_wait_s?: number | null }
export interface AgentSupervision {
  agent: string; runtime: string; project?: string | null;
  role: { id: string; name: string; source: 'agent' | 'project' | string } | null;
  override: Override; effective: Policy; gate: GateInfo;
}
export interface Supervision {
  person: string; armed: boolean; gate_mode: GateMode;
  default: Policy & { source?: string };
  levels: Level[]; wait_choices: number[]; categories: Category[]; roles: Role[];
  projects: ProjectInfo[]; agents: AgentSupervision[];
}

/** The fallbacks when the server has not sent its own (they match GET /supervision's `levels`). */
const LEVEL_LABELS: Record<LevelId, string> = { watch_closely: 'Watch closely', check_in: 'Check in', let_them_run: 'Let them run' };
export const LEVEL_ORDER: readonly LevelId[] = ['watch_closely', 'check_in', 'let_them_run'];
export const DEFAULT_WAIT_CHOICES: readonly number[] = [0, 60, 120, 300, 540];

export function levelLabel(id: string | null | undefined, levels?: readonly Level[]): string {
  if (!id) return '';
  return levels?.find((l) => l.id === id)?.label ?? LEVEL_LABELS[id as LevelId] ?? id;
}

/** "When something is held": 0 files the card and the agent carries on; otherwise it waits in place that long. */
export function waitLabel(s: number): string {
  if (!s) return "Don't wait (agent carries on)";
  return s % 60 === 0 ? `Wait up to ${s / 60} min` : `Wait up to ${s} s`;
}

const GATE_BADGES: Record<string, string> = {
  protected: 'Protected', not_seen: 'Not protected yet', not_supported: "Can't be gated yet", observing: 'Observing only', off: 'Gate is off',
};
export function gateBadge(g: GateInfo | null | undefined): string {
  if (!g) return GATE_BADGES.not_seen;
  return GATE_BADGES[g.status] ?? g.label ?? GATE_BADGES.not_seen;
}
export const GATE_MODE_LABEL: Record<GateMode, string> = { off: 'Off', observe: 'Observe only', enforce: 'On' };

// ---- shape guards ----
const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? v as T[] : []);
const errOf = (d: unknown, fallback: string): string => (isObj(d) && typeof d.error === 'string' ? d.error : fallback);
export type Result<T> = { ok: true; value: T } | { ok: false; error: string; status?: number };

function policyOf(v: unknown): Policy {
  const p = isObj(v) ? v : {};
  return {
    level: (LEVEL_ORDER as readonly string[]).includes(p.level as string) ? p.level as LevelId : 'let_them_run',
    high_risk: arr<string>(p.high_risk).filter((x) => typeof x === 'string'),
    hold_wait_s: typeof p.hold_wait_s === 'number' ? p.hold_wait_s : 0,
  };
}

function roleOf(v: unknown): Role | null {
  if (!isObj(v) || typeof v.id !== 'string' || typeof v.name !== 'string') return null;
  const used = isObj(v.used_by) ? v.used_by : {};
  return {
    ...(v as unknown as Role), ...policyOf(v),
    used_by: { projects: Number(used.projects) || 0, agents: Number(used.agents) || 0 },
    grants: Array.isArray(v.grants) ? arr<unknown>(v.grants).map(grantOf).filter((g): g is Grant => !!g) : undefined,
  };
}

function grantOf(v: unknown): Grant | null {
  if (!isObj(v) || typeof v.id !== 'string' || typeof v.scope !== 'string' || typeof v.category !== 'string') return null;
  return { ...(v as unknown as Grant), narrow: isObj(v.narrow) ? v.narrow as Narrow : {}, effect: v.effect === 'deny' ? 'deny' : 'allow' };
}

function agentOf(v: unknown): AgentSupervision | null {
  if (!isObj(v) || typeof v.agent !== 'string') return null;
  return {
    agent: v.agent, runtime: typeof v.runtime === 'string' ? v.runtime : '',
    project: typeof v.project === 'string' ? v.project : null,
    role: isObj(v.role) && typeof v.role.id === 'string' ? v.role as AgentSupervision['role'] : null,
    override: isObj(v.override) ? v.override as Override : {},
    effective: policyOf(v.effective),
    gate: isObj(v.gate) && typeof v.gate.status === 'string' ? v.gate as unknown as GateInfo : { status: 'not_seen' },
  };
}

/** GET /api/supervision → the page's whole state, or why it could not be read. */
export function parseSupervision(json: unknown): Result<Supervision> {
  if (!isObj(json) || json.ok === false || !Array.isArray(json.agents) || !Array.isArray(json.roles)) {
    return { ok: false, error: errOf(json, 'Could not read supervision') };
  }
  const d = json;
  const mode = d.gate_mode === 'off' || d.gate_mode === 'observe' || d.gate_mode === 'enforce' ? d.gate_mode : 'off';
  const levels = arr<Level>(d.levels).filter((l) => isObj(l) && typeof l.id === 'string' && typeof l.label === 'string');
  return {
    ok: true,
    value: {
      person: typeof d.person === 'string' ? d.person : '',
      armed: d.armed === true,
      gate_mode: mode,
      default: { ...policyOf(d.default), source: isObj(d.default) && typeof d.default.source === 'string' ? d.default.source : undefined },
      levels: levels.length ? levels : LEVEL_ORDER.map((id) => ({ id, label: LEVEL_LABELS[id], detail: '' })),
      wait_choices: arr<number>(d.wait_choices).filter((n) => typeof n === 'number').length
        ? arr<number>(d.wait_choices).filter((n) => typeof n === 'number') : [...DEFAULT_WAIT_CHOICES],
      categories: arr<Category>(d.categories).filter((c) => isObj(c) && typeof c.id === 'string')
        .map((c) => ({ ...c, label: typeof c.label === 'string' ? c.label : c.id, actions: arr<CategoryAction>(c.actions) })),
      roles: arr<unknown>(d.roles).map(roleOf).filter((r): r is Role => !!r),
      projects: arr<ProjectInfo>(d.projects).filter((p) => isObj(p) && typeof p.key === 'string')
        .map((p) => ({ key: p.key, label: typeof p.label === 'string' ? p.label : p.key, role_id: typeof p.role_id === 'string' ? p.role_id : null, agents: arr<string>(p.agents) })),
      agents: arr<unknown>(d.agents).map(agentOf).filter((a): a is AgentSupervision => !!a),
    },
  };
}

export function parseGrants(json: unknown): Result<Grant[]> {
  if (!isObj(json) || json.ok === false || !Array.isArray(json.grants)) return { ok: false, error: errOf(json, 'Could not read permissions') };
  return { ok: true, value: json.grants.map(grantOf).filter((g): g is Grant => !!g) };
}

export function parseRoles(json: unknown): Result<Role[]> {
  if (!isObj(json) || json.ok === false || !Array.isArray(json.roles)) return { ok: false, error: errOf(json, 'Could not read roles') };
  return { ok: true, value: json.roles.map(roleOf).filter((r): r is Role => !!r) };
}

// ---- requests ----
/** The words for an error the server sends as a code. */
const ERROR_TEXT: Record<string, string> = {
  not_armed: 'Supervision is not switched on for this server yet',
  not_allowed: 'Only an admin can change this',
  name_taken: 'A role with that name exists',
  role_in_use: 'This role is in use',
  last_role: 'The last role cannot be deleted',
  widen_not_allowed: 'A permission can only be narrowed here',
  deny_is_immutable: 'Revoke it and add a new one instead',
  not_active: 'This permission is no longer active',
  invalid: 'That value is not allowed',
  'gateway unreachable': 'The server is not answering',
};
export function errorText(code: string): string { return ERROR_TEXT[code] ?? code; }

async function call<T>(path: string, init: RequestInit | undefined, parse: (d: unknown) => Result<T>, fetchFn: typeof fetch): Promise<Result<T>> {
  try {
    const r = await fetchFn(path, init);
    const d: unknown = await r.json().catch(() => null);
    if (!r.ok) return { ok: false, error: errorText(errOf(d, `HTTP ${r.status}`)), status: r.status };
    return parse(d);
  } catch (e) { return { ok: false, error: e instanceof Error ? e.message : 'network' }; }
}
const json = (method: string, body?: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});
const enc = encodeURIComponent;
const BASE = '/api/supervision';
const okOnly = (): Result<true> => ({ ok: true, value: true });
const grantIn = (d: unknown): Result<Grant> => {
  const g = isObj(d) ? grantOf(d.grant) : null;
  return g ? { ok: true, value: g } : { ok: false, error: 'Could not read the permission' };
};
const roleIn = (d: unknown): Result<Role> => {
  const r = isObj(d) ? roleOf(d.role ?? d) : null;
  return r ? { ok: true, value: r } : { ok: false, error: 'Could not read the role' };
};

export const fetchSupervision = (f: typeof fetch = fetch) => call(BASE, undefined, parseSupervision, f);
export const putDefault = (body: Partial<Policy>, f: typeof fetch = fetch) => call(`${BASE}/default`, json('PUT', body), parseSupervision, f);
/** `role_id`/`level`/`high_risk`/`hold_wait_s`: omitted = unchanged, null = follow the role, project or default. */
export const putAgent = (agent: string, body: { role_id?: string | null } & { [K in keyof Policy]?: Policy[K] | null }, f: typeof fetch = fetch) =>
  call(`${BASE}/agents/${enc(agent)}`, json('PUT', body), parseSupervision, f);
export const putProjectRole = (key: string, roleId: string, f: typeof fetch = fetch) =>
  call(`${BASE}/projects/${enc(key)}`, json('PUT', { role_id: roleId }), parseSupervision, f);

export interface RoleDraft { name: string; description?: string; level: LevelId; high_risk: string[]; hold_wait_s: number }
export const fetchRoles = (f: typeof fetch = fetch) => call(`${BASE}/roles`, undefined, parseRoles, f);
export const createRole = (r: RoleDraft, f: typeof fetch = fetch) => call(`${BASE}/roles`, json('POST', r), roleIn, f);
export const updateRole = (id: string, r: Partial<RoleDraft>, f: typeof fetch = fetch) => call(`${BASE}/roles/${enc(id)}`, json('PUT', r), roleIn, f);
export const duplicateRole = (id: string, name: string, f: typeof fetch = fetch) => call(`${BASE}/roles/${enc(id)}/duplicate`, json('POST', { name }), roleIn, f);
export const deleteRole = (id: string, f: typeof fetch = fetch) => call(`${BASE}/roles/${enc(id)}`, json('DELETE'), okOnly, f);

export interface GrantQuery { agent?: string; scope?: GrantScope; state?: 'active' | 'pending' | 'all' }
export function fetchGrants(q: GrantQuery = {}, f: typeof fetch = fetch) {
  const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => !!v) as [string, string][]).toString();
  return call(`${BASE}/grants${qs ? `?${qs}` : ''}`, undefined, parseGrants, f);
}
export interface NewGrantBody { scope: GrantScope; scope_ref: string | null; category: string; narrow: Narrow; effect: 'allow' | 'deny' }
export const createGrant = (body: NewGrantBody, f: typeof fetch = fetch) => call(`${BASE}/grants`, json('POST', body), grantIn, f);
export const narrowGrant = (id: string, narrow: Narrow, f: typeof fetch = fetch) => call(`${BASE}/grants/${enc(id)}`, json('PATCH', { narrow }), grantIn, f);
export const revokeGrant = (id: string, f: typeof fetch = fetch) => call(`${BASE}/grants/${enc(id)}`, json('DELETE'), grantIn, f);
export const putGateMode = (mode: GateMode, f: typeof fetch = fetch) =>
  call(`${BASE}/gate/mode`, json('PUT', { mode }), (d) => (isObj(d) && typeof d.mode === 'string' ? { ok: true, value: d.mode as GateMode } : { ok: false, error: 'Could not read the mode' }), f);
/** A pending chat grant's confirm card: 1 Confirm, 2 Don't (the card's own options; CLI `orchestra grant propose`). */
export const answerGrantCard = (cardId: string, confirm: boolean, f: typeof fetch = fetch) =>
  call(`/api/approvals/unified/${enc(cardId)}/answer`, json('POST', { option_n: confirm ? '1' : '2' }), okOnly, f);

// ---- timeout rules (part 2b; part 1's /api/attention/rules, Settings › Timeout rules, lib/rulesPage.ts) ----

export interface StoredRule extends TimeoutRule { id: string }
export interface RulesState {
  person: string; armed: boolean; rules: StoredRule[]; available_actions: AvailableAction[];
  min_after_s: { no_progress: number; unanswered: number };
}

function ruleOf(v: unknown): StoredRule | null {
  if (!isObj(v) || typeof v.id !== 'string' || (v.trigger !== 'unanswered' && v.trigger !== 'no_progress')
      || typeof v.after_s !== 'number' || !Array.isArray(v.actions)) return null;
  const str = (x: unknown) => (typeof x === 'string' && x ? x : null);
  return {
    id: v.id, name: str(v.name), trigger: v.trigger, after_s: v.after_s,
    scope_agent: str(v.scope_agent), scope_kind: str(v.scope_kind) as StoredRule['scope_kind'],
    scope_risk: str(v.scope_risk) as StoredRule['scope_risk'],
    actions: arr<unknown>(v.actions).filter((a) => isObj(a) && typeof a.type === 'string') as StoredRule['actions'],
    enabled: v.enabled ? 1 : 0, is_default: v.is_default ? 1 : 0,
  };
}

/** GET /api/attention/rules → the person's rules, the actions the server offers and each trigger's minimum. */
export function parseRules(json: unknown): Result<RulesState> {
  if (!isObj(json) || json.ok === false || !Array.isArray(json.rules)) return { ok: false, error: errOf(json, 'Could not read the rules') };
  const min = isObj(json.min_after_s) ? json.min_after_s : {};
  return {
    ok: true,
    value: {
      person: typeof json.person === 'string' ? json.person : '', armed: json.armed !== false,
      rules: json.rules.map(ruleOf).filter((r): r is StoredRule => !!r),
      available_actions: arr<AvailableAction>(json.available_actions).filter((a) => isObj(a) && typeof a.type === 'string')
        .map((a) => ({ type: a.type, available: a.available === true, why: typeof a.why === 'string' ? a.why : null })),
      min_after_s: { no_progress: Number(min.no_progress) || 600, unanswered: Number(min.unanswered) || 60 },
    },
  };
}

const RULES = '/api/attention/rules';
const ruleIn = (d: unknown): Result<StoredRule> => {
  const r = isObj(d) ? ruleOf(d.rule) : null;
  return r ? { ok: true, value: r } : { ok: false, error: 'Could not read the rule' };
};
const RULE_ERRORS: Record<string, string> = {
  unknown_agent: 'That agent is not registered', unknown_person: 'Handing a card to a person comes with multi-user',
  bad_by: 'Choose who decides: the agent itself or its supervisor', unsafe_scope: 'Never used on permission cards or high-risk questions',
  action_not_available: 'That action is not available yet', not_armed: 'Timeout rules are not switched on for this server yet',
};
async function ruleCall<T>(path: string, init: RequestInit | undefined, parse: (d: unknown) => Result<T>, f: typeof fetch): Promise<Result<T>> {
  const r = await call(path, init, parse, f);
  if (r.ok) return r;
  return { ...r, error: RULE_ERRORS[r.error] ?? r.error };
}
export const fetchRules = (f: typeof fetch = fetch) => ruleCall(RULES, undefined, parseRules, f);
/** Create (no id) or update a rule; `body` is lib/rulesPage.ts ruleBody(). */
export const saveRule = (id: string | null, body: Record<string, unknown>, f: typeof fetch = fetch) =>
  ruleCall(id ? `${RULES}/${enc(id)}` : RULES, json(id ? 'PUT' : 'POST', body), ruleIn, f);
export const setRuleEnabled = (id: string, enabled: boolean, f: typeof fetch = fetch) =>
  ruleCall(`${RULES}/${enc(id)}`, json('PUT', { enabled }), ruleIn, f);
export const deleteRule = (id: string, f: typeof fetch = fetch) => ruleCall(`${RULES}/${enc(id)}`, json('DELETE'), okOnly, f);
