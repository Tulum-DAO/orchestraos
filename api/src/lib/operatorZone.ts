/**
 * The operator's CURRENT time zone, for clients that show them times: <data dir>/state/shaw-location.json
 * `{tz, label}` (the file the fleet keeps as the one source of where the operator is; it changes when they travel).
 * Read on every call, never cached. Null when the file is absent, unreadable, names no tz, or names one this runtime
 * cannot format: a client then keeps the install's own zone setting. No zone is ever hardcoded here.
 *
 * Only a CANONICAL IANA zone (or UTC) is served: this runtime's Intl constructor also takes aliases a browser may
 * reject or map elsewhere (EST resolves to America/Panama; EST5EDT, SystemV/EST5, +05:00), and a label on the wrong
 * hour is worse than no field.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

export interface OperatorZone { tz: string; label: string }

export function operatorZone(dataDir: string): OperatorZone | null {
  let d: any;
  try { d = JSON.parse(readFileSync(join(dataDir, 'state', 'shaw-location.json'), 'utf-8')); } catch { return null; }
  const tz = typeof d?.tz === 'string' ? d.tz.trim() : '';
  if (!tz) return null;
  if (!isCanonicalZone(tz)) return null;
  const label = typeof d.label === 'string' && d.label.trim() ? d.label.trim() : tz;
  return { tz, label };
}

function isCanonicalZone(tz: string): boolean {
  if (tz === 'UTC') return true;
  try {
    if (!Intl.supportedValuesOf('timeZone').includes(tz)) return false;
    return new Intl.DateTimeFormat('en-US', { timeZone: tz }).resolvedOptions().timeZone === tz;
  } catch { return false; }
}
