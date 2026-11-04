/**
 * notifyPolicy.ts — Settings › Notifications: the person's choices (state/users/<you>/prefs.json, written through the
 * settings registry) and the one decision, "may an alert go to them now?". The TypeScript twin of
 * scripts/notify_policy.py, which is what the server's push path asks (approval_notify.py); both run every case in
 * contract/notify-policy-cases.json, so a sender added on this side later decides exactly as the Python one.
 *
 *   notify_level   'all' | 'needs_me' | 'nothing'   absent or unknown = 'all' (what every install did before the page)
 *   notify_agents  {"<agent id>": level}             one agent's own level; it wins over notify_level
 *   quiet_hours    {from: "HH:MM", to: "HH:MM"}       in the install time zone (empty = UTC); null or from == to = off
 *
 * decide() says why: 'ok' send now; 'suppressed' the person does not want it (the sender marks it handled and never
 * sends it later); 'quiet' hold it until quiet hours end. The level is checked before quiet hours. An agent's own
 * level matches its rotated names: "dev-3" applies to "dev-3-gen4" (exact id first, then the -genN root).
 */
export const NOTIFY_LEVELS = ['all', 'needs_me', 'nothing'] as const;
export type NotifyLevel = typeof NOTIFY_LEVELS[number];
export const DEFAULT_NOTIFY_LEVEL: NotifyLevel = 'all';
/** What an alert is about. Only needs_me (a card or question waiting on you) has a sender today. */
export type NotifyEvent = 'needs_me' | 'finished' | 'failed' | 'missed_call';
export type NotifyDecision = 'ok' | 'quiet' | 'suppressed';
const GEN_SUFFIX = /-gen\d+$/;   // scripts/approval.py _canon
export interface QuietHours { from: string; to: string }
export interface NotifyPrefs { notify_level?: unknown; notify_agents?: unknown; quiet_hours?: unknown }

const HHMM = /^([01]\d|2[0-3]):([0-5]\d)$/;
/** An agent id as the install names seats (and approval rows' from_agent). */
export const AGENT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
export const MAX_AGENT_OVERRIDES = 200;

export function isLevel(v: unknown): v is NotifyLevel {
  return typeof v === 'string' && (NOTIFY_LEVELS as readonly string[]).includes(v);
}

export function levelFor(prefs: NotifyPrefs | null | undefined, agent: string | null | undefined): NotifyLevel {
  const p = prefs && typeof prefs === 'object' ? prefs : {};
  const agents = p.notify_agents;
  if (agent && agents && typeof agents === 'object' && !Array.isArray(agents)) {
    for (const name of [agent, agent.trim().replace(GEN_SUFFIX, '')]) {
      const own = (agents as Record<string, unknown>)[name];
      if (isLevel(own)) return own;
    }
  }
  return isLevel(p.notify_level) ? p.notify_level : DEFAULT_NOTIFY_LEVEL;
}

function minutes(v: unknown): number | null {
  const m = typeof v === 'string' ? HHMM.exec(v) : null;
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

/** Minutes past midnight of `now` in `tz` (an empty or unknown zone is UTC). */
function localMinutes(now: Date, tz: string): number {
  let zone = 'UTC';
  if (tz) { try { new Intl.DateTimeFormat('en-US', { timeZone: tz }); zone = tz; } catch { /* unknown zone: UTC */ } }
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: zone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(now);
  const n = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  return n('hour') * 60 + n('minute');
}

export function inQuietHours(quiet: unknown, now: Date, tz: string): boolean {
  if (!quiet || typeof quiet !== 'object' || Array.isArray(quiet)) return false;
  const q = quiet as { from?: unknown; to?: unknown };
  const start = minutes(q.from), end = minutes(q.to);
  if (start === null || end === null || start === end) return false;
  const t = localMinutes(now, tz);
  return start < end ? t >= start && t < end : t >= start || t < end;
}

/** The decision, with its reason: Nothing suppresses all; Needs me suppresses all but needs_me; quiet hours hold. */
export function decide(prefs: NotifyPrefs | null | undefined, event: string, agent: string | null | undefined, now: Date, tz: string): NotifyDecision {
  const p = prefs && typeof prefs === 'object' && !Array.isArray(prefs) ? prefs : {};
  const level = levelFor(p, agent);
  if (level === 'nothing' || (level === 'needs_me' && event !== 'needs_me')) return 'suppressed';
  return inQuietHours(p.quiet_hours, now, tz) ? 'quiet' : 'ok';
}

export function shouldAlert(prefs: NotifyPrefs | null | undefined, event: string, agent: string | null | undefined, now: Date, tz: string): boolean {
  return decide(prefs, event, agent, now, tz) === 'ok';
}

// ------------------------------------------------------------------------------------------- what a save may hold

export function validQuietHours(v: unknown): string | null {
  if (v === null) return null;
  const q = v as { from?: unknown; to?: unknown } | null;
  if (!q || typeof q !== 'object' || Array.isArray(q) || Object.keys(q).some((k) => k !== 'from' && k !== 'to')
      || minutes(q.from) === null || minutes(q.to) === null) return 'Quiet hours: a start and an end time, like 22:00 and 07:00';
  if (q.from === q.to) return 'Quiet hours: the start and the end must differ';
  return null;
}

export function validOverrides(v: unknown): string | null {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return 'Pick an agent and All, Needs me or Nothing';
  const e = Object.entries(v as Record<string, unknown>);
  if (e.length > MAX_AGENT_OVERRIDES) return `At most ${MAX_AGENT_OVERRIDES} agents`;
  for (const [id, lvl] of e) {
    if (!AGENT_ID_RE.test(id)) return `Not an agent name: ${id.slice(0, 64)}`;
    if (!isLevel(lvl)) return `${id}: pick All, Needs me or Nothing`;
  }
  return null;
}

/** The overrides as stored, with anything malformed left out. */
export function readOverrides(v: unknown): Record<string, NotifyLevel> {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return {};
  return Object.fromEntries(Object.entries(v as Record<string, unknown>).filter(([id, l]) => AGENT_ID_RE.test(id) && isLevel(l))) as Record<string, NotifyLevel>;
}

export function readQuietHours(v: unknown): QuietHours | null {
  return v !== null && v !== undefined && validQuietHours(v) === null ? { from: (v as QuietHours).from, to: (v as QuietHours).to } : null;
}
