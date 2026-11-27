/**
 * The operator's CURRENT time zone, for the client that shows them times: <data dir>/state/shaw-location.json
 * `{tz, label}` (the file the fleet keeps as the one source of where the operator is; it changes when they travel).
 * Read on every call, never cached. Null when the file is absent, unreadable, names no tz, or names one a browser
 * cannot be trusted with: a client then keeps the install's own zone setting. No zone is ever hardcoded here.
 *
 * Served to the install's ADMIN only (operatorZoneFor): where the operator is never reaches a client-scoped tenant or
 * an install member, and their dashboards keep the install zone.
 *
 * What counts as a zone: `Area/Location` (not Etc/ or SystemV/) that this runtime lists among its supported zones,
 * by that name or by its RESOLVED id, or UTC. This runtime's Intl constructor also takes aliases a browser may reject or map elsewhere
 * (EST resolves to America/Panama; EST5EDT, +05:00): those are not served. ICU and IANA disagree on the canonical
 * spelling of some zones (Asia/Kolkata and Asia/Calcutta, Europe/Kyiv and Europe/Kiev): either spelling resolves to a
 * supported id, so either is served, as the file spells it.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { Request } from 'express';
import { installRole, principal } from './principal.js';
import type { TailscaleUser } from './tailscale-identity.js';

export interface OperatorZone { tz: string; label: string }

const LABEL_MAX = 64;
const AREA_LOCATION = /^[A-Z][A-Za-z]+\/[A-Za-z0-9_+\-]+(\/[A-Za-z0-9_+\-]+)?$/;
let supported: Set<string> | null = null;   // the runtime's list, a few hundred strings: computed once per process

export function operatorZone(dataDir: string): OperatorZone | null {
  let d: any;
  try { d = JSON.parse(readFileSync(join(dataDir, 'state', 'shaw-location.json'), 'utf-8')); } catch { return null; }
  if (!d) return null;   // JSON null (any other non-object JSON has no tz and falls out below)
  const tz = typeof d.tz === 'string' ? d.tz.trim() : '';
  if (!tz || !isZone(tz)) return null;
  const label = typeof d.label === 'string' && d.label.trim() ? cut(d.label.trim()) : tz;
  return { tz, label };
}

/** The zone for the caller of a route: the install's admin (lib/principal installRole: the Tailscale login's role on the
 *  install, or a trusted proxy's asserted role; a caller with no login is a member) outside any client scope gets it;
 *  everyone else gets null. `users` is for tests (the tailscale-users list). */
export function operatorZoneFor(req: Request, dataDir: string, users?: TailscaleUser[]): OperatorZone | null {
  if (installRole(req, users) !== 'admin') return null;
  if (principal(req)?.clientScope) return null;
  return operatorZone(dataDir);
}

/** The first LABEL_MAX characters (by code point, never inside one), trimmed again. */
function cut(label: string): string {
  return Array.from(label).slice(0, LABEL_MAX).join('').trim();
}

function isZone(tz: string): boolean {
  if (tz === 'UTC') return true;
  if (!AREA_LOCATION.test(tz) || tz.startsWith('Etc/') || tz.startsWith('SystemV/')) return false;
  try {
    if (!supported) supported = new Set(Intl.supportedValuesOf('timeZone'));
    const resolved = new Intl.DateTimeFormat('en-US', { timeZone: tz }).resolvedOptions().timeZone;
    return supported.has(resolved) || supported.has(tz);
  } catch { return false; }
}
