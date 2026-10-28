/**
 * Whether the desktop sidebar is collapsed on Arturo's home, remembered in this browser like the
 * sidebar's Chats and Agents sections. Only "/" honours it: every other page has no button to bring
 * the sidebar back, so it always shows there. Shared by the layout (which hides the sidebar) and the
 * home's menu button (which flips it and names the state), live across both and across tabs.
 */
const KEY = 'orchestra.sidebar.collapsed';
const EVENT = 'orchestra:sidebar-collapsed';

// Reading or writing localStorage can THROW (private windows, blocked site data); the in-memory copy
// keeps the button working for this page even then.
let mem: boolean | null = null;

export function sidebarCollapsed(): boolean {
  if (mem === null) {
    try { mem = localStorage.getItem(KEY) === '1'; } catch { mem = false; }
  }
  return mem;
}

export function setSidebarCollapsed(v: boolean): void {
  mem = v;
  try { localStorage.setItem(KEY, v ? '1' : '0'); } catch { /* this page still follows */ }
  try { window.dispatchEvent(new Event(EVENT)); } catch { /* no window (tests) */ }
}

export function subscribeSidebarCollapsed(cb: () => void): () => void {
  const other = (e: StorageEvent) => { if (e.key === KEY) { mem = e.newValue === '1'; cb(); } };
  window.addEventListener(EVENT, cb);
  window.addEventListener('storage', other);
  return () => { window.removeEventListener(EVENT, cb); window.removeEventListener('storage', other); };
}
