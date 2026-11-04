/**
 * settingsApi.ts — the server's Settings rows (GET /api/settings, PUT /api/settings/:key; api routes/settings.ts) and
 * the words a row shows about its last change. The live store is stores/serverSettings.ts.
 */
/** `description`: a one-line word on the option (the voice provider's trade-off), when the server has one. */
export interface SettingChoice { value: string; label: string; available: boolean; reason?: string; description?: string }
export interface SettingRow {
  key: string; section: string; scope: 'user' | 'everyone'; value: unknown;
  editable: boolean; readOnly: boolean; source: string;
  changedBy: string | null; changedAt: string | null; note: string | null; detail: string | null;
  choices?: SettingChoice[];
}
export interface SettingChangeEvent { key: string; value: unknown; changedBy: string; changedAt: string }
type Role = 'admin' | 'member';

export function parseSettings(json: unknown): { ok: true; role: Role; rows: SettingRow[] } | { ok: false; error: string } {
  const d = json as { ok?: boolean; role?: unknown; rows?: unknown; error?: unknown } | null;
  if (!d || d.ok === false || !Array.isArray(d.rows)) return { ok: false, error: typeof d?.error === 'string' ? d.error : 'Could not read the settings' };
  // Only an explicit "admin" unlocks Everyone rows (Always member; lib/myRole.ts).
  return { ok: true, role: d.role === 'admin' ? 'admin' : 'member', rows: d.rows as SettingRow[] };
}

export async function fetchSettings(fetchFn: typeof fetch = fetch): Promise<ReturnType<typeof parseSettings>> {
  try {
    const r = await fetchFn('/api/settings');
    return parseSettings(await r.json().catch(() => null));
  } catch (e) { return { ok: false, error: e instanceof Error ? e.message : 'network' }; }
}

export async function saveServerSetting(key: string, value: unknown, fetchFn: typeof fetch = fetch):
    Promise<{ ok: true; row: SettingRow } | { ok: false; error: string }> {
  try {
    const r = await fetchFn(`/api/settings/${encodeURIComponent(key)}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }),
    });
    const d = await r.json().catch(() => ({})) as { ok?: boolean; row?: SettingRow; error?: string };
    return r.ok && d.ok !== false && d.row ? { ok: true, row: d.row } : { ok: false, error: d.error || `HTTP ${r.status}` };
  } catch (e) { return { ok: false, error: e instanceof Error ? e.message : 'network' }; }
}

export function applyChange(row: SettingRow, c: SettingChangeEvent): SettingRow {
  return { ...row, value: c.value, changedBy: c.changedBy, changedAt: c.changedAt };
}

/** A refused save: the row to put back (the one before it), or null when the row no longer holds the value this save
 *  put there (a change pushed from another dashboard came in meanwhile, and is kept). Compared by identity, so an
 *  object value (the default model map) counts as changed when a new one arrived. */
export function revertRow(current: SettingRow | undefined, optimistic: unknown, before: SettingRow): SettingRow | null {
  return current && Object.is(current.value, optimistic) ? before : null;
}

/** agents.defaultModel: a save sends {runtime, model}; the row shows at once the held map with that runtime changed. */
export function mergeDefaultModel(held: unknown, runtime: string, model: string): Record<string, string> {
  return { ...(held && typeof held === 'object' ? held as Record<string, string> : {}), [runtime]: model };
}

/** agents.defaultEffort: a save sends {runtime, effort}; the row shows at once the held map with that runtime changed
 *  (null: taken out, new agents start on the CLI's default). */
export function mergeDefaultEffort(held: unknown, runtime: string, effort: string | null): Record<string, string> {
  const out = { ...(held && typeof held === 'object' ? held as Record<string, string> : {}) };
  if (effort === null) delete out[runtime]; else out[runtime] = effort;
  return out;
}

/** "sam@example.com" -> "Sam". */
export function whoName(by: string): string {
  const local = by.split('@')[0] || by;
  return local.charAt(0).toUpperCase() + local.slice(1);
}

export function formatChangedBy(by: string | null, at: string | null, now: number): string | null {
  if (!by || !at) return null;
  const t = Date.parse(at);
  if (Number.isNaN(t)) return null;
  const s = Math.max(0, Math.round((now - t) / 1000));
  const ago = s < 60 ? 'just now' : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : `${Math.floor(s / 86400)} d ago`;
  return `Changed by ${whoName(by)} · ${ago}`;
}

export function timeZoneOptions(current: string): { value: string; label: string }[] {
  const zones = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : [];
  const list = zones.includes(current) || !current ? zones : [current, ...zones];
  // Not set: every clock shows the viewer's own device time (lib/messageTime), so it says so.
  return [{ value: '', label: "This device's time zone (not set)" }, ...list.map((z) => ({ value: z, label: z.replace(/_/g, ' ') }))];
}

export type SelectOption = { value: string; label: string; disabled?: boolean };
/** Keep the current value selectable even when the list does not have it (yet). */
export function withCurrent(options: SelectOption[], value: unknown): SelectOption[] {
  const v = typeof value === 'string' ? value : '';
  return !v || options.some((o) => o.value === v) ? options : [{ value: v, label: v }, ...options];
}
