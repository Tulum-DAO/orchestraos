/**
 * avatarLooks.ts — each agent's saved avatar look (GET/PUT/DELETE /api/arturo/avatar[s]) and the hub that
 * every avatar of that agent reads. A pick shows everywhere at once, then is put back with the server's
 * reason if the server refuses it. Agents with no saved look keep the default (hash shape, ink, auto eyes)
 * and are `chosen: false`, which is what offers them "Pick a look".
 */
import { SWATCHES, type AgentHub } from '../components/avatar/avatarCore.ts';

const SHAPE_NAMES: Record<string, string> = {
  circle: 'Circle', square: 'Square', pill: 'Pill', hexagon: 'Hexagon', heart: 'Heart', droplet: 'Droplet', star: 'Star',
  pebble: 'Pebble', gumdrop: 'Gumdrop', bean: 'Bean', cloud: 'Cloud', bunny: 'Bunny', kitty: 'Kitty', egg: 'Egg',
  flower: 'Flower', sprout: 'Sprout',
};
/** "Star", "Heart": a shape's name for people. */
export const shapeName = (k: string | undefined): string => (k && SHAPE_NAMES[k]) || 'Default';
/** "Ink", "Blue": a swatch's name for people. */
export const colorName = (hex: string | undefined): string =>
  SWATCHES.find((s) => s.hex.toLowerCase() === (hex || '').toLowerCase())?.name || 'Ink';

export interface Look { shape: string; color: string; eyeColor: string }
export type LookPatch = Partial<Look>;

const LOOK_KEYS = ['shape', 'color', 'eyeColor'] as const;

/** Every saved look into the hub: those agents are chosen. Agents not listed keep their default. */
export function applySavedLooks(hub: AgentHub, saved: Record<string, Partial<Look>>): void {
  for (const [id, rec] of Object.entries(saved)) {
    const look: LookPatch = {};
    for (const k of LOOK_KEYS) if (typeof rec?.[k] === 'string') look[k] = rec[k] as string;
    hub.set(id, { ...look, chosen: true });
  }
}

export async function fetchSavedLooks(): Promise<Record<string, Partial<Look>> | null> {
  try {
    const r = await fetch('/api/arturo/avatars');
    if (!r.ok) return null;
    const d = await r.json();
    return d?.ok && d.avatars && typeof d.avatars === 'object' ? d.avatars : null;
  } catch { return null; }
}

// The last saved looks the server gave, kept in this browser only so a page load draws each agent in its
// saved look from the first frame, instead of its default look until /avatars answers. The server's answer
// always replaces it (a look changed or reset on another device corrects itself a moment later).
export const LOOKS_CACHE_KEY = 'orchestra.avatar.savedLooks';
type Store = Pick<Storage, 'getItem' | 'setItem'>;
const browserStore = (): Store | null => { try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch { return null; } };

export function readCachedLooks(s: Store | null = browserStore()): Record<string, Partial<Look>> {
  try {
    const v = JSON.parse(s?.getItem(LOOKS_CACHE_KEY) || '{}');
    return v && typeof v === 'object' && !Array.isArray(v) ? v : {};
  } catch { return {}; }
}
export function writeCachedLooks(saved: Record<string, Partial<Look>>, s: Store | null = browserStore()): void {
  try { s?.setItem(LOOKS_CACHE_KEY, JSON.stringify(saved)); } catch { /* private mode: the server's answer still applies */ }
}
function cacheOne(agent: string, look: Partial<Look> | null, s: Store | null = browserStore()): void {
  const all = readCachedLooks(s);
  if (look) all[agent] = look; else delete all[agent];
  writeCachedLooks(all, s);
}

/** Before anything is drawn: the looks this browser last saw saved. */
export function applyCachedLooks(hub: AgentHub, s: Store | null = browserStore()): void {
  applySavedLooks(hub, readCachedLooks(s));
}

let loaded: Promise<void> | null = null;
/** Once per page: the server's saved looks into the hub, and into this browser's copy. An agent this browser
 *  remembered as saved that the server no longer has (reset elsewhere) goes back to its default look. */
export function loadSavedLooksOnce(hub: AgentHub, defaultOf?: (agent: string) => Look, s: Store | null = browserStore()): Promise<void> {
  if (!loaded) loaded = fetchSavedLooks().then((saved) => {
    if (!saved) { loaded = null; return; }
    for (const id of Object.keys(readCachedLooks(s))) if (!(id in saved) && defaultOf) hub.set(id, { ...defaultOf(id), chosen: false });
    applySavedLooks(hub, saved);
    writeCachedLooks(saved, s);
  });
  return loaded;
}

export type Result = { ok: true; body?: Record<string, unknown> } | { ok: false; error: string };

export async function putLook(agent: string, patch: LookPatch): Promise<Result> {
  return send('PUT', '/api/arturo/avatar', JSON.stringify({ agent, ...patch }));
}
export async function resetLook(agent: string): Promise<Result> {
  return send('DELETE', `/api/arturo/avatar?agent=${encodeURIComponent(agent)}`);
}
async function send(method: string, url: string, body?: string): Promise<Result> {
  try {
    const r = await fetch(url, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body });
    const d = await r.json().catch(() => ({}));
    return r.ok && d.ok !== false ? { ok: true, body: d } : { ok: false, error: d.error || `HTTP ${r.status}` };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'network' };
  }
}

/** A pick: shown everywhere at once (and chosen), kept if the server agrees, put back with its reason if not. */
export async function pickLook(hub: AgentHub, agent: string, patch: LookPatch,
  put: (agent: string, patch: LookPatch) => Promise<Result> = putLook): Promise<string | null> {
  const prev = hub.get(agent);
  const before = { shape: prev?.shape, color: prev?.color, eyeColor: prev?.eyeColor, chosen: prev?.chosen ?? false };
  hub.set(agent, { ...patch, chosen: true });
  const r = await put(agent, patch);
  if (r.ok) {
    const now = hub.get(agent);
    cacheOne(agent, { shape: now?.shape, color: now?.color, eyeColor: now?.eyeColor });
    return null;
  }
  hub.set(agent, before);
  return `Couldn't save the look: ${r.error}`;
}

/** Reset: back to the default look and not chosen; put back as it was if the server refuses. */
export async function resetToDefault(hub: AgentHub, agent: string, def: Look,
  reset: (agent: string) => Promise<Result> = resetLook): Promise<string | null> {
  const prev = hub.get(agent);
  const before = { shape: prev?.shape, color: prev?.color, eyeColor: prev?.eyeColor, chosen: prev?.chosen ?? false };
  hub.set(agent, { ...def, chosen: false });
  const r = await reset(agent);
  if (r.ok) { cacheOne(agent, null); return null; }
  hub.set(agent, before);
  return `Couldn't reset the look: ${r.error}`;
}

/** Anyone may change any agent's look for now (no per-agent ownership yet): the one place that will decide it. */
export function canChangeLook(): boolean {
  return true;
}

export const DISMISS_KEY = (agent: string) => `orchestra.avatar.pickDismissed.${agent}`;

/** "Pick a look for …": while the agent has no saved look, for someone who can save one, unless they
 *  dismissed it on this device. */
export function showsLookCard(o: { chosen: boolean | undefined; canSave: boolean; dismissed: boolean }): boolean {
  return !o.chosen && o.canSave && !o.dismissed;
}

export function readDismissed(storage: Pick<Storage, 'getItem'> | null, agent: string): boolean {
  try { return storage?.getItem(DISMISS_KEY(agent)) === '1'; } catch { return false; }
}
export function writeDismissed(storage: Pick<Storage, 'setItem'> | null, agent: string): void {
  try { storage?.setItem(DISMISS_KEY(agent), '1'); } catch { /* not remembered; hidden for now */ }
}
