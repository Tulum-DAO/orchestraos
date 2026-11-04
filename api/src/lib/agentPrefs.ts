/**
 * agentPrefs.ts — how each person sees each agent, saved on the server per person
 * (state/agent-prefs.json: {"users": {"<username>": {"<agent id>": {"style": "bubbles", "accent": "#3b82f6"}}}}),
 * so the choice follows you across devices the way pins do. Telemetry and history stay keyed by agent id; this is
 * only the view. Atomic tmp-replace writes; a missing or bad file reads as no preferences.
 *
 * `style_before_dev` is the style Back to chat returns to. The server records it, never a client: a write that turns
 * the style to 'dev' saves the style it leaves (the chosen one, else the default), so no client can forget it and it
 * survives restarts and follows the person to another device. It is never accepted in a change, and it never touches
 * `style` itself.
 */
import fs from 'fs';
import path from 'path';
import { readUserPrefs } from './userPrefs.js';

export type ChatStyle = 'modern' | 'bubbles' | 'dev';
export const CHAT_STYLES: readonly ChatStyle[] = ['modern', 'bubbles', 'dev'];

export interface AgentPrefs { style?: ChatStyle; accent?: string; style_before_dev?: ChatStyle }

/** The fields a person may change; `style_before_dev` is the server's own. */
type ChangeField = 'style' | 'accent';

const AGENT_RE = /^[A-Za-z0-9_.:-]{1,80}$/;
const ACCENT_RE = /^#[0-9a-fA-F]{6}$/;
// Arturo is Modern until someone picks otherwise; every other agent is Bubbles.
const DEFAULT_STYLE: Record<string, ChatStyle> = { arturo: 'modern' };

function file(orchestraDir: string): string {
  return path.join(orchestraDir, 'state', 'agent-prefs.json');
}

function readAll(orchestraDir: string): { users: Record<string, Record<string, AgentPrefs>> } {
  try {
    const d = JSON.parse(fs.readFileSync(file(orchestraDir), 'utf8'));
    return d && typeof d === 'object' && d.users && typeof d.users === 'object' ? d : { users: {} };
  } catch { return { users: {} }; }
}

export function isAgentId(id: unknown): id is string {
  return typeof id === 'string' && AGENT_RE.test(id);
}

/** The known fields of a change, checked; a reason when anything in it is wrong. `null` clears a field. */
export function cleanPrefsChange(raw: unknown): { change: Partial<Record<ChangeField, string | null>> } | { error: string } {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return { error: 'expected {style?, accent?}' };
  const r = raw as Record<string, unknown>;
  const change: Partial<Record<ChangeField, string | null>> = {};
  if ('style' in r) {
    if (r.style !== null && !CHAT_STYLES.includes(r.style as ChatStyle)) return { error: `style must be one of ${CHAT_STYLES.join(', ')}` };
    change.style = r.style as string | null;
  }
  if ('accent' in r) {
    if (r.accent !== null && !(typeof r.accent === 'string' && ACCENT_RE.test(r.accent))) return { error: 'accent must be a #rrggbb colour' };
    change.accent = r.accent as string | null;
  }
  if (!Object.keys(change).length) return { error: 'nothing to change' };
  return { change };
}

// The person's own default for agents they have not styled (Settings › General, "You"); Arturo keeps his own.
const userDefaultStyle = (orchestraDir: string, username: string): ChatStyle | undefined => {
  const v = readUserPrefs(orchestraDir, username).default_chat_style;
  return v === 'modern' || v === 'bubbles' ? v : undefined;
};
const defaultStyle = (agentId: string, userDefault?: ChatStyle): ChatStyle => DEFAULT_STYLE[agentId] ?? userDefault ?? 'bubbles';
const validStyle = (s: unknown): ChatStyle | null => (CHAT_STYLES.includes(s as ChatStyle) ? (s as ChatStyle) : null);

/** What this person sees for this agent, with the defaults filled in, and which fields they chose themselves.
 *  `style_before_dev` is never 'dev': with nothing recorded it is the agent's default. */
export function readPrefs(orchestraDir: string, username: string, agentId: string):
    { style: ChatStyle; accent: string | null; style_before_dev: ChatStyle; chosen: ChangeField[] } {
  const ud = userDefaultStyle(orchestraDir, username);
  const own = (readAll(orchestraDir).users[username] || {})[agentId] || {};
  const style = validStyle(own.style);
  const accent = typeof own.accent === 'string' && ACCENT_RE.test(own.accent) ? own.accent : null;
  const before = validStyle(own.style_before_dev);
  return {
    style: style ?? defaultStyle(agentId, ud),
    accent,
    style_before_dev: before && before !== 'dev' ? before : defaultStyle(agentId, ud),
    chosen: [...(style ? ['style' as const] : []), ...(accent ? ['accent' as const] : [])],
  };
}

export function writePrefs(orchestraDir: string, username: string, agentId: string,
                           change: Partial<Record<ChangeField, string | null>>): void {
  const all = readAll(orchestraDir);
  const mine = (all.users[username] ||= {});
  const rec: Record<string, unknown> = { ...(mine[agentId] || {}) };
  // Entering Dev: keep the style it leaves for Back to chat. Already in Dev, the earlier record stands.
  if (change.style === 'dev') {
    const leaving = validStyle(rec.style) ?? defaultStyle(agentId, userDefaultStyle(orchestraDir, username));
    if (leaving !== 'dev') rec.style_before_dev = leaving;
  }
  for (const [k, v] of Object.entries(change)) {
    if (k !== 'style' && k !== 'accent') continue;
    if (v === null) delete rec[k];
    else rec[k] = v;
  }
  if (Object.keys(rec).length) mine[agentId] = rec as AgentPrefs;
  else delete mine[agentId];
  const f = file(orchestraDir);
  fs.mkdirSync(path.dirname(f), { recursive: true });
  const tmp = `${f}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(all));
  fs.renameSync(tmp, f);
}
