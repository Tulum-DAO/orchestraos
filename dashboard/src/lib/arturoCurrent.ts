/**
 * arturoCurrent.ts — the ONE current Arturo thread in this browser, shared by the floating pane and the home.
 *
 * They used to keep separate ones (`orchestra.arturo.pill.conversation` and `orchestra.arturo.conversation`), so a
 * call in the pane and a call on the home were two conversations that never saw each other. Now both read and
 * write this one: switching threads (or starting a new one) in either switches both, live, in this tab and in
 * any other open tab. Every old thread stays in the list to switch back to.
 */
const KEY = 'orchestra.arturo.conversation';        // the home's key: its current thread carries over as is
const OLD_PANE_KEY = 'orchestra.arturo.pill.conversation';
const EVENT = 'orchestra:arturo-thread';

function read(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

/** This browser's current thread, or null when there is none yet. */
export function currentThread(): string | null {
  return read(KEY) || read(OLD_PANE_KEY) || null;
}

/** Make `id` the current thread everywhere. No-op when it already is (so followers never echo). */
export function setCurrentThread(id: string): void {
  if (!id || read(KEY) === id) return;
  try { localStorage.setItem(KEY, id); } catch { /* private mode: this view still switches */ }
  try { window.dispatchEvent(new CustomEvent(EVENT, { detail: id })); } catch { /* no window (tests) */ }
}

/** Follow the current thread when another view (or tab) switches it. Returns the unsubscribe. */
export function subscribeCurrentThread(fn: (id: string) => void): () => void {
  const local = (e: Event) => { const id = (e as CustomEvent<string>).detail; if (id) fn(id); };
  const other = (e: StorageEvent) => { if (e.key === KEY && e.newValue) fn(e.newValue); };
  window.addEventListener(EVENT, local);
  window.addEventListener('storage', other);
  return () => { window.removeEventListener(EVENT, local); window.removeEventListener('storage', other); };
}
