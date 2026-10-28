/**
 * assistantName.ts — what the assistant is called on this install: "Arturo" until an admin renames it.
 *
 * The name is a server setting (services/arturo/assistant_name.py), carried on /api/arturo/health as
 * `assistant_name` and changed through /api/arturo/name, so every browser and device shows the same one.
 * Only the NAME changes: the id stays "arturo" (routes, the agent id, storage keys).
 *
 * The last name the server gave is kept in this browser too, only so a reload shows it at once instead of
 * flashing the default until health answers; the server's answer always wins.
 */
import { useSyncExternalStore } from 'react';

export const DEFAULT_ASSISTANT_NAME = 'Arturo';
export const NAME_MAX = 32;
const KEY = 'orchestra.assistantName';

type Storage = Pick<globalThis.Storage, 'getItem' | 'setItem' | 'removeItem'>;
const storage = (): Storage | null => { try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch { return null; } };

let current = readCached(storage());
const listeners = new Set<() => void>();

export function readCached(s: Storage | null): string {
  try {
    const v = s?.getItem(KEY);
    return v && v.trim() && v.length <= NAME_MAX ? v : DEFAULT_ASSISTANT_NAME;
  } catch { return DEFAULT_ASSISTANT_NAME; }
}

export function assistantName(): string { return current; }

/** The server said what the name is (health, or a save): every surface showing it updates. */
export function applyAssistantName(name: unknown, s: Storage | null = storage()): void {
  const n = typeof name === 'string' && name.trim() ? name.trim().slice(0, NAME_MAX) : DEFAULT_ASSISTANT_NAME;
  try {
    if (n === DEFAULT_ASSISTANT_NAME) s?.removeItem(KEY); else s?.setItem(KEY, n);
  } catch { /* private mode: this page still updates */ }
  if (n === current) return;
  current = n;
  listeners.forEach((l) => l());
}

export function subscribeAssistantName(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

export function useAssistantName(): string {
  return useSyncExternalStore(subscribeAssistantName, assistantName, () => DEFAULT_ASSISTANT_NAME);
}

/** Text written with the default name, in the given one ("Arturo" as a whole word), for the few lines kept
 *  as constants (the turned-off notice, the admin-only note). */
export function named(text: string, name: string): string {
  return name === DEFAULT_ASSISTANT_NAME ? text : text.replace(/\bArturo\b/g, () => name);
}

/** Only an admin renames the assistant (it shows on every device and in its own instructions); anyone else
 *  sees the name read-only. A missing user is the single-user install, its operator. */
export function canRename(user: { role?: string } | null | undefined): boolean {
  return !user || user.role === 'admin';
}

export type NameResult = { ok: true; name: string } | { ok: false; error: string };

async function call(method: 'PUT' | 'DELETE', body?: unknown): Promise<NameResult> {
  try {
    const res = await fetch('/api/arturo/name', {
      method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined,
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok || !json.ok) return { ok: false, error: json.error || `Could not save the name (HTTP ${res.status})` };
    applyAssistantName(json.name);
    return { ok: true, name: json.name };
  } catch {
    return { ok: false, error: 'Could not reach the server' };
  }
}

/** Rename; on success every surface shows the new name. The server's reason comes back on a refusal. */
export function saveAssistantName(name: string): Promise<NameResult> { return call('PUT', { name }); }

/** Back to "Arturo". */
export function resetAssistantName(): Promise<NameResult> { return call('DELETE'); }
