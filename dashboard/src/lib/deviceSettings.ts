/**
 * deviceSettings.ts — "This device" settings (Settings › Display), live: one cached reading of localStorage, one
 * setter that stores, applies (CSS variables and attributes on <html>, lib/displaySettings.ts) and tells every
 * reader, the same page and other tabs alike. useDeviceSetting is the one hook a row reads it through.
 * Never touches window or document at import (node tests import modules that import this).
 */
import { useSyncExternalStore } from 'react';
import { applyDisplay, readDisplay, writeDisplay, textAvatarPx, DISPLAY_DEFAULTS, DISPLAY_KEYS, DISPLAY_RESET_KEYS, type Display } from './displaySettings.ts';

const EVENT = 'orchestra:display';
type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const storage = (): Store | null => { try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch { return null; } };

let cache: Display | null = null;

export function display(): Display {
  if (!cache) cache = readDisplay(storage());
  return cache;
}

function publish(next: Display): void {
  cache = next;
  if (typeof document !== 'undefined') applyDisplay(next, document.documentElement);
  try { window.dispatchEvent(new Event(EVENT)); } catch { /* no window (tests) */ }
}

export function setDeviceSetting<K extends keyof Display>(key: K, value: Display[K]): void {
  writeDisplay(storage(), key, value);
  publish({ ...display(), [key]: value });
}

/** A sidebar width shown while its edge is dragged: the CSS variable only. Nothing is stored and no reader is told
 *  until the drag ends (setDeviceSetting then), so a drag never re-renders the app or writes storage per move. */
export function previewSidebarWidth(px: number): void {
  if (typeof document !== 'undefined') document.documentElement.style.setProperty('--sidebar-w', `${px}px`);
}

/** Settings › Display "Reset display settings": the rows shown on Display back to their defaults on this device
 *  (DISPLAY_RESET_KEYS); General's text size, agent steps and timestamps, and the send keys, stay. */
export function resetDisplay(): void {
  const s = storage();
  const next: Record<string, unknown> = { ...display() };
  for (const k of DISPLAY_RESET_KEYS) { writeDisplay(s, k, DISPLAY_DEFAULTS[k]); next[k] = DISPLAY_DEFAULTS[k]; }
  publish(next as unknown as Display);
}

/** Apply what this device has stored (main.tsx before the first render; the QA harness too). */
export function applyStoredDisplay(): void {
  if (typeof document !== 'undefined') applyDisplay(display(), document.documentElement);
}

let otherTabs = false;
export function subscribeDisplay(cb: () => void): () => void {
  if (typeof window === 'undefined') return () => {};
  if (!otherTabs) {
    otherTabs = true;
    const keys = Object.values(DISPLAY_KEYS);
    window.addEventListener('storage', (e) => { if (e.key && keys.includes(e.key)) { cache = null; publish(display()); } });
  }
  window.addEventListener(EVENT, cb);
  return () => window.removeEventListener(EVENT, cb);
}

export function useDisplay(): Display {
  return useSyncExternalStore(subscribeDisplay, display, () => DISPLAY_DEFAULTS);
}

export function useDeviceSetting<K extends keyof Display>(key: K): [Display[K], (v: Display[K]) => void] {
  const d = useDisplay();
  return [d[key], (v: Display[K]) => setDeviceSetting(key, v)];
}

/** An avatar beside text: its base size scaled by the avatar rule (never for avatars that are controls). */
export function useTextAvatar(base: number): number {
  return textAvatarPx(base, useDisplay().textScale);
}
