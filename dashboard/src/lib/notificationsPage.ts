/**
 * notificationsPage.ts — what Settings › Notifications shows and saves (components/settings/sections/
 * NotificationsSection.tsx). The rows are the person's own (You): notifications.level, .agentOverrides, .quietHours on
 * GET/PUT /api/settings (api lib/settingsRegistry.ts, lib/notifyPolicy.ts). The server's push path decides from the same
 * file (scripts/notify_policy.py). Pure, so node tests run it.
 */
export type NotifyLevel = 'all' | 'needs_me' | 'nothing';
export const LEVEL_OPTIONS: readonly { value: NotifyLevel; label: string }[] = [
  { value: 'all', label: 'All' }, { value: 'needs_me', label: 'Needs me' }, { value: 'nothing', label: 'Nothing' },
];
export interface QuietHours { from: string; to: string }
/** What turning quiet hours on starts with. */
export const DEFAULT_QUIET: QuietHours = { from: '22:00', to: '07:00' };
const HHMM = /^([01]\d|2[0-3]):([0-5]\d)$/;

export const isLevel = (v: unknown): v is NotifyLevel => v === 'all' || v === 'needs_me' || v === 'nothing';

export function overridesOf(v: unknown): Record<string, NotifyLevel> {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return {};
  return Object.fromEntries(Object.entries(v as Record<string, unknown>).filter(([, l]) => isLevel(l))) as Record<string, NotifyLevel>;
}

export interface AgentChoice { id: string; name: string }

/** The /api/agents payload (either shape) as id + name, sorted by name; registered agents only. */
export function agentChoices(data: unknown): AgentChoice[] {
  const list = Array.isArray(data) ? data : (data as { agents?: unknown } | null | undefined)?.agents;
  const rows = (Array.isArray(list) ? list : []) as { id?: unknown; name?: unknown; unregistered?: unknown }[];
  const seen = new Set<string>();
  const out: AgentChoice[] = [];
  for (const a of rows) {
    // An unregistered pane ("unregistered:<name>") or another machine's id ("mac:foo") is not an agent cards come
    // from, and the server refuses ids with a colon.
    if (typeof a?.id !== 'string' || !a.id || a.id.includes(':') || a.unregistered === true || seen.has(a.id)) continue;
    seen.add(a.id);
    out.push({ id: a.id, name: typeof a.name === 'string' && a.name.trim() ? a.name.trim() : a.id });
  }
  return out.sort((x, y) => x.name.localeCompare(y.name));
}

/** The override rows, by name; an agent that is gone from the install keeps its row (under its id) so it can be removed. */
export function overrideRows(overrides: Record<string, NotifyLevel>, agents: AgentChoice[]): { id: string; name: string; level: NotifyLevel }[] {
  const names = new Map(agents.map((a) => [a.id, a.name]));
  return Object.entries(overrides).map(([id, level]) => ({ id, name: names.get(id) ?? id, level }))
    .sort((x, y) => x.name.localeCompare(y.name));
}

/** Agents that can still be given their own setting (not already listed, not the one being added). */
export function addableAgents(agents: AgentChoice[], overrides: Record<string, NotifyLevel>, adding: string | null): AgentChoice[] {
  return agents.filter((a) => !(a.id in overrides) && a.id !== adding);
}

export const withOverride = (o: Record<string, NotifyLevel>, id: string, level: NotifyLevel) => ({ ...o, [id]: level });
export function withoutOverride(o: Record<string, NotifyLevel>, id: string): Record<string, NotifyLevel> {
  const rest = { ...o };
  delete rest[id];
  return rest;
}

export function quietOf(v: unknown): QuietHours | null {
  const q = v as { from?: unknown; to?: unknown } | null;
  return q && typeof q === 'object' && typeof q.from === 'string' && typeof q.to === 'string' && HHMM.test(q.from) && HHMM.test(q.to)
    ? { from: q.from, to: q.to } : null;
}

/** One end of quiet hours changed: the value to save, or the reason it can't be. An incomplete time (a browser's time
 *  field mid-typing gives "") is not saved. */
export function changeQuiet(cur: QuietHours, end: 'from' | 'to', time: string): { ok: true; value: QuietHours } | { ok: false; error: string | null } {
  if (!HHMM.test(time)) return { ok: false, error: null };
  const next = { ...cur, [end]: time };
  if (next.from === next.to) return { ok: false, error: 'The start and the end must differ' };
  return { ok: true, value: next };
}

/** The line that says which clock quiet hours use: the install's zone, or UTC while none is set (the push path reads
 *  [operator] timezone; empty = UTC, scripts/notify_policy.py). */
export function quietZoneLine(installZone: string): string {
  return installZone
    ? `Times are in the install's time zone, ${installZone.replace(/_/g, ' ')}.`
    : "Times are in UTC: the install's time zone isn't set (Settings › Time zone).";
}

/** This browser: the dashboard shows no system notifications (no Notification API, no service worker), so this device
 *  has nothing to set. Said once, pointing to where alerts do come from. */
export const THIS_DEVICE_NOTE = "This browser doesn't show pop-up alerts. Cards waiting on you show in the bell at the top; alerts come from the OrchestraOS watch app.";
