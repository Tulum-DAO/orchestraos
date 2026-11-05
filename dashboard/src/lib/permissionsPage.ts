/**
 * Settings › Permissions helpers (components/settings/sections/PermissionsSection.tsx): a role read as one line, the
 * project rows, the standing grants grouped by level and read as sentences, what a grant may be narrowed by, and the
 * add-a-permission and role-editor checks. Labels come from GET /supervision; the verbs below are sentence glue.
 */
import { levelLabel, type Category, type Grant, type GrantScope, type LevelId, type NewGrantBody, type Role, type Supervision } from './supervision.ts';

/** What a grant lets an agent do, as a verb phrase ("may <verb> without asking"); by action, else by category. */
const ACTION_VERBS: Record<string, string> = {
  deploy: 'deploy', push: 'push code', publish: 'publish packages or pages', message: 'send email or messages',
  http_write: 'send data to websites', remote_shell: 'run commands on other machines', spend: 'use paid APIs and payments',
  connector_write: 'change things in connected apps',
  delete_files: 'delete files', force_push: 'force-push', drop_data: 'delete data', kill_process: 'stop programs',
  system_settings: 'change system settings', unrecognised: "run commands the gate doesn't recognise",
  edit_outside: 'edit files outside its folder', secrets: 'touch secrets or keys',  // pragma: allowlist secret
  npm: 'install npm packages', pip: 'install Python packages', system: 'install system packages',
  script_pipe: 'run install scripts from the internet', other: 'use other installers',
};
const CATEGORY_VERBS: Record<string, string> = {
  outside_world: 'take outside-world actions', destructive: 'take destructive actions',
  outside_folder: 'work outside its folder', install: 'install packages',
};

const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

/** "a", "a and b", "a, b and c"; phrases that share their first or last words say them once:
 *  install npm packages + install Python packages → "install npm and Python packages". */
export function joinPhrases(phrases: readonly string[]): string {
  if (phrases.length < 2) return phrases[0] ?? '';
  const words = phrases.map((p) => p.split(' '));
  const shortest = Math.min(...words.map((w) => w.length));
  let pre = 0;
  while (pre < shortest - 1 && words.every((w) => w[pre] === words[0][pre])) pre++;
  let suf = 0;
  while (suf < shortest - 1 - pre && words.every((w) => w[w.length - 1 - suf] === words[0][words[0].length - 1 - suf])) suf++;
  const mids = words.map((w) => w.slice(pre, w.length - suf).join(' '));
  const list = mids.length === 2 ? `${mids[0]} and ${mids[1]}` : `${mids.slice(0, -1).join(', ')} and ${mids[mids.length - 1]}`;
  return [...words[0].slice(0, pre), list, ...words[0].slice(words[0].length - suf)].join(' ');
}

export function categoryLabel(id: string, cats: readonly Category[]): string {
  return cats.find((c) => c.id === id)?.label ?? id.replace(/_/g, ' ');
}

/** The verb phrase for a grant's category and narrowing: "deploy", "edit files outside its folder in /srv/site". */
export function grantVerb(g: Pick<Grant, 'category' | 'narrow'>, cats: readonly Category[]): string {
  const action = g.narrow?.action;
  let verb = action ? ACTION_VERBS[action] : CATEGORY_VERBS[g.category];
  if (!verb) {
    const label = action ? cats.find((c) => c.id === g.category)?.actions.find((a) => a.id === action)?.label : categoryLabel(g.category, cats);
    verb = `do ${lowerFirst(label ?? action ?? g.category)}`;
  }
  if (g.narrow?.folder) verb += ` in ${g.narrow.folder}`;
  if (g.narrow?.recipient) verb += ` to ${g.narrow.recipient}`;
  return verb;
}

/** Who a grant is for, at the start of its sentence. `names`: role ids and project keys → their names. */
export function grantSubject(g: Pick<Grant, 'scope' | 'scope_ref'>, names: Record<string, string> = {}): string {
  const ref = g.scope_ref ?? '';
  switch (g.scope) {
    case 'fleet': return 'All agents';
    case 'role': return `${names[ref] ?? ref} agents`;
    case 'project': return `Agents in ${names[ref] ?? ref.split('/').filter(Boolean).pop() ?? ref}`;
    case 'session': return `Session ${ref.slice(0, 8)}`;
    default: return ref;
  }
}

/** "dev may deploy without asking"; a deny: "dev may not deploy without asking". */
export function grantLabel(g: Grant, cats: readonly Category[], names: Record<string, string> = {}): string {
  return `${grantSubject(g, names)} ${g.effect === 'deny' ? 'may not' : 'may'} ${grantVerb(g, cats)} without asking`;
}

/** The note under a grant row: where it came from, and when. */
export function grantSource(g: Grant, now = new Date()): string {
  const day = g.created_at ? new Date(g.created_at) : null;
  const when = day && !Number.isNaN(day.getTime())
    ? day.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', ...(day.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}) })
    : null;
  const tail = when ? ` · ${when}` : '';
  switch (g.source) {
    case 'card': return `from a permission card${tail}`;
    case 'chat': return g.said ? `you said in chat: '${g.said}'${tail}` : `from chat${tail}`;
    case 'starter': return 'starter role';
    case 'role_copy': return `copied with its role${tail}`;
    case 'settings': return `added in Settings${tail}`;
    default: return when ?? '';
  }
}

/** One line for a role: its level, what waits under it, what it may do without asking.
 *  "Check in · asks before destructive and outside-world actions · may install npm and Python packages". */
export function roleSummary(r: Pick<Role, 'level' | 'high_risk' | 'grants'> & { grants?: Grant[] }, cats: readonly Category[], levels?: Parameters<typeof levelLabel>[1]): string {
  const parts = [levelLabel(r.level, levels)];
  if (r.level === 'let_them_run') parts.push('nothing waits for you');
  else if (r.level === 'watch_closely') parts.push('asks before anything risky');
  else if (r.high_risk.length) parts.push(`asks before ${joinPhrases(r.high_risk.map((id) => lowerFirst(categoryLabel(id, cats))))}`);
  else parts.push('nothing marked high-risk');
  const allows = (r.grants ?? []).filter((g) => g.effect === 'allow' && g.state === 'active');
  if (allows.length) parts.push(`may ${joinPhrases(allows.map((g) => grantVerb(g, cats)))}`);
  return parts.join(' · ');
}

/** "used by 3 projects · 1 agent", or "not used". */
export function usedByLine(r: Pick<Role, 'used_by'>): string {
  const n = (k: number, one: string) => `${k} ${one}${k === 1 ? '' : 's'}`;
  const bits = [r.used_by.projects ? n(r.used_by.projects, 'project') : '', r.used_by.agents ? n(r.used_by.agents, 'agent') : ''].filter(Boolean);
  return bits.length ? `used by ${bits.join(' · ')}` : 'not used';
}
export const roleInUse = (r: Pick<Role, 'used_by'>) => r.used_by.projects + r.used_by.agents > 0;

/** The role editor's check: null when it can be saved. `names`: the other roles' names. */
export function validateRole(d: { name: string }, names: readonly string[] = []): string | null {
  const name = d.name.trim();
  if (!name) return 'Give the role a name';
  if (names.some((n) => n.trim().toLowerCase() === name.toLowerCase())) return 'A role with that name exists';
  return null;
}

export interface ProjectRow { key: string; label: string; roleId: string | null; roleName: string; agents: string[] }
export function projectRows(s: Pick<Supervision, 'projects' | 'roles'>): ProjectRow[] {
  return s.projects.map((p) => ({
    key: p.key, label: p.label, roleId: p.role_id, agents: p.agents,
    roleName: s.roles.find((r) => r.id === p.role_id)?.name ?? '',
  }));
}

/** Role ids and project keys → their names, for grantLabel. */
export function scopeNames(s: Pick<Supervision, 'projects' | 'roles'>): Record<string, string> {
  return Object.fromEntries([...s.roles.map((r) => [r.id, r.name]), ...s.projects.map((p) => [p.key, p.label])]);
}

export const GRANT_LEVELS: readonly { scope: GrantScope; title: string }[] = [
  { scope: 'fleet', title: 'Whole server' }, { scope: 'project', title: 'Projects' }, { scope: 'role', title: 'Roles' },
  { scope: 'agent', title: 'Agents' }, { scope: 'session', title: 'Sessions' },
];
/** The grants in Settings order (whole server, projects, roles, agents, sessions); a level with none is left out. */
export function grantsByLevel(grants: readonly Grant[]): { scope: GrantScope; title: string; grants: Grant[] }[] {
  return GRANT_LEVELS.map((l) => ({ ...l, grants: grants.filter((g) => g.scope === l.scope) })).filter((l) => l.grants.length);
}

export type NarrowOption = { kind: 'action'; actions: { id: string; label: string }[] } | { kind: 'folder' } | { kind: 'recipient' };
/** What a grant in this category may be narrowed to: one of its actions, and a folder (outside-folder and destructive)
 *  or a recipient (outside-world). */
export function narrowOptions(category: string, cats: readonly Category[]): NarrowOption[] {
  const out: NarrowOption[] = [{ kind: 'action', actions: cats.find((c) => c.id === category)?.actions ?? [] }];
  if (category === 'outside_folder' || category === 'destructive') out.push({ kind: 'folder' });
  if (category === 'outside_world') out.push({ kind: 'recipient' });
  return out;
}

export interface GrantDraft { scope: GrantScope; ref?: string; category: string; action?: string; folder?: string; recipient?: string }
const PICK: Partial<Record<GrantScope, string>> = { agent: 'Pick an agent', role: 'Pick a role', project: 'Pick a project', session: 'Pick a session' };
/** The add-a-permission check: null when it can be added. */
export function validateNewGrant(d: GrantDraft): string | null {
  if (d.scope !== 'fleet' && !d.ref?.trim()) return PICK[d.scope] ?? 'Pick who it is for';
  if (!d.category) return 'Pick what they may do';
  const folder = d.folder?.trim();
  if (folder && !folder.startsWith('/')) return 'Use a full folder path';
  if (folder && folder.split('/').includes('..')) return 'Use a full folder path';
  return null;
}

export function newGrantBody(d: GrantDraft): NewGrantBody {
  const narrow: NewGrantBody['narrow'] = {};
  if (d.action) narrow.action = d.action;
  if (d.folder?.trim()) narrow.folder = d.folder.trim();
  if (d.recipient?.trim()) narrow.recipient = d.recipient.trim();
  return { scope: d.scope, scope_ref: d.scope === 'fleet' ? null : d.ref?.trim() ?? null, category: d.category, narrow, effect: 'allow' };
}

/** The level choice's options, from the server's levels. */
export function levelOptions(levels: readonly { id: LevelId; label: string }[]): { value: LevelId; label: string }[] {
  return levels.map((l) => ({ value: l.id, label: l.label }));
}
