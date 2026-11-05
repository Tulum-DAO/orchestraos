/**
 * agentsPage.ts — Settings › Agents beyond the registry's rows (api routes/settings-agents.ts):
 *   - "N of M use their own" under each default (GET /api/settings/agents/overrides): which agents keep their own value;
 *   - Background processes (GET …/processes, POST …/processes/stop): programs agents started and left running.
 * Pure helpers and fetchers here, so node can test them; the page is components/settings/sections/AgentsSection.tsx.
 */

export interface AgentOverride { id: string; value: boolean | number }
export interface Overrides { total: number; agents: string[]; rows: Record<string, AgentOverride[]>; notCounted: Record<string, string> }

/** A listed program. A member sees `program` (its file name only); `detail` (with its script), `command` (secrets
 *  masked) and `commandHash` come to admins only. */
export interface BackgroundProcess {
  pid: number; startTicks: number; startedAt: string; program: string; detail?: string; command?: string; commandHash?: string; ports: number[];
  agent: string | null; via: string | null;
}
export interface ProcessList { supported: boolean; canStop: boolean; processes: BackgroundProcess[] }

type Result<T> = { ok: true; value: T } | { ok: false; error: string };

const errorOf = (d: unknown, status?: number) => {
  const e = (d as { error?: unknown } | null)?.error;
  return typeof e === 'string' && e ? e : status ? `HTTP ${status}` : 'Could not read the server';
};

export function parseOverrides(json: unknown): Result<Overrides> {
  const d = json as Partial<Overrides> & { ok?: boolean } | null;
  if (!d || d.ok === false || typeof d.total !== 'number' || !d.rows || typeof d.rows !== 'object') return { ok: false, error: errorOf(d) };
  const rows: Record<string, AgentOverride[]> = {};
  for (const [k, list] of Object.entries(d.rows)) {
    rows[k] = (Array.isArray(list) ? list : []).filter((o): o is AgentOverride =>
      !!o && typeof o.id === 'string' && (typeof o.value === 'boolean' || typeof o.value === 'number'));
  }
  return { ok: true, value: { total: d.total, agents: Array.isArray(d.agents) ? d.agents.filter((a) => typeof a === 'string') : [], rows,
    notCounted: d.notCounted && typeof d.notCounted === 'object' ? d.notCounted : {} } };
}

export function parseProcesses(json: unknown): Result<ProcessList> {
  const d = json as Partial<ProcessList> & { ok?: boolean } | null;
  if (!d || d.ok === false || !Array.isArray(d.processes)) return { ok: false, error: errorOf(d) };
  const processes = d.processes.filter((p): p is BackgroundProcess => !!p && Number.isInteger(p.pid) && Number.isInteger(p.startTicks)
    && typeof p.program === 'string' && typeof p.startedAt === 'string' && Array.isArray(p.ports));
  // Only an explicit true offers Stop (the server refuses a member anyway).
  return { ok: true, value: { supported: d.supported !== false, canStop: d.canStop === true, processes } };
}

async function getJson(url: string, fetchFn: typeof fetch): Promise<{ status: number; body: unknown } | { error: string }> {
  try {
    const r = await fetchFn(url);
    return { status: r.status, body: await r.json().catch(() => null) };
  } catch (e) { return { error: e instanceof Error ? e.message : 'network' }; }
}

export async function fetchOverrides(fetchFn: typeof fetch = fetch): Promise<Result<Overrides>> {
  const r = await getJson('/api/settings/agents/overrides', fetchFn);
  return 'error' in r ? { ok: false, error: r.error } : parseOverrides(r.body ?? { error: `HTTP ${r.status}` });
}

export async function fetchProcesses(fetchFn: typeof fetch = fetch): Promise<Result<ProcessList>> {
  const r = await getJson('/api/settings/agents/processes', fetchFn);
  return 'error' in r ? { ok: false, error: r.error } : parseProcesses(r.body ?? { error: `HTTP ${r.status}` });
}

/** Stop one program: the server checks it is still the same one (pid, start time, its hash of the command) before it
 *  signals. Only an admin's list carries the hash; without one there is nothing to send. */
export async function stopProcess(p: Pick<BackgroundProcess, 'pid' | 'startTicks' | 'commandHash'>, fetchFn: typeof fetch = fetch):
    Promise<{ ok: true; result: string; warning?: string } | { ok: false; error: string }> {
  if (!p.commandHash) return { ok: false, error: 'Only an admin can stop a program' };
  try {
    const r = await fetchFn('/api/settings/agents/processes/stop', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pid: p.pid, startTicks: p.startTicks, commandHash: p.commandHash }),
    });
    const d = await r.json().catch(() => null) as { ok?: boolean; result?: unknown; warning?: unknown } | null;
    if (!r.ok || d?.ok === false) return { ok: false, error: errorOf(d, r.status) };
    return { ok: true, result: typeof d?.result === 'string' ? d.result : 'stopped', ...(typeof d?.warning === 'string' && d.warning ? { warning: d.warning } : {}) };
  } catch (e) { return { ok: false, error: e instanceof Error ? e.message : 'network' }; }
}

/** "2 of 5" and the words after it; null when there is nothing to count against (no agent has these settings). */
export function overrideCount(list: readonly AgentOverride[] | undefined, total: number): { n: number; of: string; rest: string } | null {
  if (!list || total <= 0) return null;
  return { n: list.length, of: `${list.length} of ${total}`, rest: list.length === 1 && total === 1 ? ' uses its own' : ' use their own' };
}

/** An agent's own value, in the row's words: On / Off, or a percentage for Compact at. */
export function overrideValueText(key: string, value: boolean | number): string {
  if (typeof value === 'boolean') return value ? 'On' : 'Off';
  return key === 'agents.compactAt' ? `${value}%` : String(value);
}

/** The program's command, shortened to `max` characters with an ellipsis (the full line is shown on hover and on expand). */
export function shortCommand(cmd: string, max = 64): string {
  const one = cmd.replace(/\s+/g, ' ').trim();
  return one.length <= max ? one : `${one.slice(0, max - 1).trimEnd()}…`;
}

export function portsText(ports: readonly number[]): string {
  if (!ports.length) return '';
  return `${ports.length === 1 ? 'Port' : 'Ports'} ${ports.join(', ')}`;
}

/** When it started, in the install's time zone (the browser's when none is set), with the zone named. */
export function startedText(iso: string, zone: string, now: Date = new Date()): string {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return '';
  const make = (o: Intl.DateTimeFormatOptions, locale = 'en-US') => {
    try { return new Intl.DateTimeFormat(locale, { ...o, timeZone: zone || undefined }); } catch { return new Intl.DateTimeFormat(locale, o); }
  };
  const day = make({ year: 'numeric', month: '2-digit', day: '2-digit' }, 'en-CA');
  const today = day.format(t) === day.format(now);
  const fmt = make({ hour: 'numeric', minute: '2-digit', timeZoneName: 'short', ...(today ? {} : { month: 'short', day: 'numeric' }) });
  return `Started ${today ? 'today ' : ''}${fmt.format(t)}`;
}

/** What a row shows: the masked command for an admin, the program name for a member. */
export const processLabel = (p: Pick<BackgroundProcess, 'program' | 'command'>) => p.command ?? p.program;

/** What Stop asks before it acts: the program and what stops working. */
export function stopConsequence(p: Pick<BackgroundProcess, 'program' | 'detail' | 'ports'>): string {
  const ports = p.ports.length ? ` on ${p.ports.length === 1 ? 'port' : 'ports'} ${p.ports.join(', ')}` : '';
  return `Stop ${shortCommand(p.detail ?? p.program, 40)}${ports}? Anything using it stops working.`;
}

/** Opening an agent's own panel from Settings: its page, with the panel open (pages/AgentPage.tsx reads the state). */
export const OPEN_AGENT_PANEL = 'openAgentPanel';
export const agentPanelPath = (id: string) => `/agent/${encodeURIComponent(id)}`;
export function wantsAgentPanel(state: unknown): boolean {
  return !!state && typeof state === 'object' && (state as Record<string, unknown>)[OPEN_AGENT_PANEL] === true;
}
