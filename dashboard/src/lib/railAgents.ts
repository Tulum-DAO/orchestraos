/**
 * railAgents.ts — pins and renames for the sidebar's agent list.
 *
 * Pins: the faces pinned at the top of the list, saved on the server per person (/api/agents/pins), so they
 * follow you across devices the way saved looks do. A copy is kept in this browser only so the row draws at once
 * on load; the server's answer replaces it. A pin or unpin shows at once and is put back if the server refuses.
 *
 * Rename: an agent's short name (/api/agents/:id/name). The server applies the name rules (unique per project,
 * old names kept forever) and its refusal comes back word for word, for the row to show inline.
 */
import { useSyncExternalStore } from 'react';

const KEY = 'orchestra.agentPins';
type Store = Pick<Storage, 'getItem' | 'setItem'>;
const browser = (): Store | null => { try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch { return null; } };

function readCached(s: Store | null): string[] {
  try {
    const v = JSON.parse(s?.getItem(KEY) || '[]');
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : [];
  } catch { return []; }
}

let pins: string[] = readCached(browser());
const listeners = new Set<() => void>();
function set(next: string[], s: Store | null = browser()) {
  pins = next;
  try { s?.setItem(KEY, JSON.stringify(next)); } catch { /* private mode: this page still updates */ }
  listeners.forEach((l) => l());
}

export function pinned(): string[] { return pins; }
export function subscribePins(fn: () => void): () => void { listeners.add(fn); return () => { listeners.delete(fn); }; }
export function usePins(): string[] { return useSyncExternalStore(subscribePins, pinned, () => []); }

let loading: Promise<void> | null = null;
/** Once per page: the server's pins replace this browser's copy. */
export function loadPinsOnce(fetchFn: typeof fetch = fetch): Promise<void> {
  if (!loading) {
    loading = fetchFn('/api/agents/pins').then((r) => r.json()).then((d) => {
      if (d?.ok && Array.isArray(d.pins)) set(d.pins);
    }).catch(() => { loading = null; });
  }
  return loading;
}

/** Pin or unpin: shown at once, saved, put back if the server refuses. Returns an error to show, or null. */
export async function togglePin(id: string, fetchFn: typeof fetch = fetch): Promise<string | null> {
  const before = pins;
  const next = before.includes(id) ? before.filter((x) => x !== id) : [...before, id];
  set(next);
  try {
    const r = await fetchFn('/api/agents/pins', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pins: next }) });
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.ok) { set(d.pins); return null; }
    set(before);
    return d.error || `Couldn't save the pin (HTTP ${r.status})`;
  } catch {
    set(before);
    return "Couldn't reach the server";
  }
}

export type RenameResult = { ok: true; name: string } | { ok: false; error: string };

/** Rename an agent's short name; the server's reason when it refuses ("'dev' is already the name of ..."). */
export async function renameAgent(id: string, name: string, fetchFn: typeof fetch = fetch): Promise<RenameResult> {
  try {
    const r = await fetchFn(`/api/agents/${encodeURIComponent(id)}/name`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
    });
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.ok) return { ok: true, name: d.name };
    return { ok: false, error: d.error || `Couldn't rename (HTTP ${r.status})` };
  } catch {
    return { ok: false, error: "Couldn't reach the server" };
  }
}

/** Long-press for touch: fires after `ms` unless the finger moves or lifts first (so scrolling never opens it).
 *  The tap the browser sends when that finger lifts is swallowed (onClickCapture), so it never also opens the row.
 *  Only that tap: a click with the finger still down or within GHOST_CLICK_MS of it lifting. A later click (a screen
 *  reader's activation, a mouse, a keyboard) always goes through, even when no tap followed the long-press. */
export function longPress(onFire: () => void, ms = 500) {
  return longPressControl(onFire, ms).handlers;
}

export const GHOST_CLICK_MS = 400;

/** longPress with its controls: `reset()` forgets a fired press (call it when what the press opened closes), and
 *  `onEnd(fired)` is told whenever the finger lifts, is cancelled, or moves away. */
export function longPressControl(onFire: () => void, ms = 500, opts: { onEnd?: (fired: boolean) => void } = {}) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let x = 0, y = 0, fired = false, down = false, upAt = 0;
  const clear = () => { if (timer) { clearTimeout(timer); timer = null; } };
  const end = () => {
    const wasDown = down;
    clear();
    down = false;
    upAt = Date.now();
    if (wasDown) opts.onEnd?.(fired);
  };
  const handlers = {
    onClickCapture: (e: { preventDefault: () => void; stopPropagation: () => void }) => {
      if (fired && (down || Date.now() - upAt <= GHOST_CLICK_MS)) { e.preventDefault(); e.stopPropagation(); }
      fired = false;
    },
    onTouchStart: (e: { touches: ArrayLike<{ clientX: number; clientY: number }> }) => {
      const t = e.touches[0]; x = t.clientX; y = t.clientY;
      clear();
      fired = false;
      down = true;
      timer = setTimeout(() => { timer = null; fired = true; onFire(); }, ms);
    },
    onTouchMove: (e: { touches: ArrayLike<{ clientX: number; clientY: number }> }) => {
      const t = e.touches[0];
      if (timer && (Math.abs(t.clientX - x) > 8 || Math.abs(t.clientY - y) > 8)) end();
    },
    onTouchEnd: end,
    onTouchCancel: end,
  };
  return { handlers, reset: () => { clear(); fired = false; } };
}
