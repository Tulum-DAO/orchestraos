/**
 * settingsLog.ts — every change made through Settings, one JSON line each, in <data dir>/state/settings-log.jsonl:
 *   {"key": "general.timeZone", "scope": "everyone", "by": "sam@…", "at": "2026-11-03T10:00:00Z", "value": "UTC"}
 * Everyone rows show their last change from here ("Changed by Sam · 2 min ago"). Append-only, owner-only (0600).
 */
import fs from 'fs';
import path from 'path';

export type SettingScope = 'user' | 'everyone';
export interface SettingChange { key: string; scope: SettingScope; by: string; at: string; value?: unknown }

const logFile = (dataDir: string) => path.join(dataDir, 'state', 'settings-log.jsonl');

export function appendSettingChange(dataDir: string, c: SettingChange): void {
  const f = logFile(dataDir);
  fs.mkdirSync(path.dirname(f), { recursive: true });
  fs.appendFileSync(f, JSON.stringify(c) + '\n', { mode: 0o600 });
}

/** appendSettingChange for a change that has ALREADY been saved: a log that can't be written (disk full, a bad path)
 *  must not turn a good save into an error, so the failure is reported to the server log and the caller goes on. */
export function recordSettingChange(dataDir: string, c: SettingChange): void {
  try { appendSettingChange(dataDir, c); }
  catch (err) { console.error(`[settings] saved ${c.key} but could not log the change:`, err instanceof Error ? err.message : err); }
}

/** The last change of each key (by, at). */
export function lastChanges(dataDir: string): Map<string, { by: string; at: string }> {
  const out = new Map<string, { by: string; at: string }>();
  let raw = '';
  try { raw = fs.readFileSync(logFile(dataDir), 'utf-8'); } catch { return out; }
  for (const line of raw.split('\n')) {
    if (!line.trim()) continue;
    try {
      const c = JSON.parse(line);
      if (c && typeof c.key === 'string' && typeof c.by === 'string' && typeof c.at === 'string') out.set(c.key, { by: c.by, at: c.at });
    } catch { /* a torn line: skip it */ }
  }
  return out;
}
