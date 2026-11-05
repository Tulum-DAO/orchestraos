/** The server's Settings rows, live: loaded once per page, saved per row, and updated when another dashboard changes
 *  an Everyone row (event: setting on /api/status/stream, stores/liveStatus.ts). */
import { create } from 'zustand';
import { applyChange, fetchSettings, type SettingChangeEvent, type SettingRow } from '../lib/settingsApi';
import { setInstallRole } from '../lib/myRole';

interface ServerSettingsState {
  rows: Record<string, SettingRow>;
  loaded: boolean;
  error: string | null;
  setAll: (rows: SettingRow[]) => void;
  put: (row: SettingRow) => void;
  patch: (key: string, p: Partial<SettingRow>) => void;
  remote: (c: SettingChangeEvent) => void;
  fail: (e: string) => void;
}

export const useServerSettings = create<ServerSettingsState>((set) => ({
  rows: {},
  loaded: false,
  error: null,
  setAll: (rows) => set({ rows: Object.fromEntries(rows.map((r) => [r.key, r])), loaded: true, error: null }),
  put: (row) => set((s) => ({ rows: { ...s.rows, [row.key]: row } })),
  patch: (key, p) => set((s) => (s.rows[key] ? { rows: { ...s.rows, [key]: { ...s.rows[key], ...p } } } : s)),
  remote: (c) => set((s) => (s.rows[c.key] ? { rows: { ...s.rows, [c.key]: applyChange(s.rows[c.key], c) } } : s)),
  fail: (e) => set({ error: e }),
}));

export async function loadServerSettings(fetchFn: typeof fetch = fetch): Promise<void> {
  const r = await fetchSettings(fetchFn);
  if (!r.ok) { useServerSettings.getState().fail(r.error); return; }
  setInstallRole(r.role);
  useServerSettings.getState().setAll(r.rows);
}

/** The install's time zone for clock times ("" = the viewer's own clock, as before). */
export function useInstallTimeZone(): string {
  return useServerSettings((s) => {
    const v = s.rows['general.timeZone']?.value;
    return typeof v === 'string' ? v : '';
  });
}
