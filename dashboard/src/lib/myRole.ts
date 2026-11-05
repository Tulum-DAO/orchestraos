/**
 * myRole.ts — this person's role on the install, as the server says it (/api/me install_role, /api/settings role).
 * Settings phase 1: Everyone settings are admin-only; anyone else sees them read-only with a lock. Only an explicit
 * "admin" from the server unlocks them: until /api/me answers, when it fails, or on an older API that sends no
 * install_role, this person is a member ("Always member").
 */
import { useSyncExternalStore } from 'react';

export type InstallRole = 'admin' | 'member';
export const ADMIN_LOCK_NOTE = 'Only an admin can change this';

let role: InstallRole = 'member';
const listeners = new Set<() => void>();

export function installRole(): InstallRole { return role; }
export function isInstallAdmin(): boolean { return role === 'admin'; }

export function setInstallRole(r: unknown): void {
  const next: InstallRole = r === 'admin' ? 'admin' : 'member';
  if (next === role) return;
  role = next;
  for (const l of [...listeners]) l();
}

export function subscribeInstallRole(cb: () => void): () => void {
  listeners.add(cb);
  return () => { listeners.delete(cb); };
}

/** The role for a component's render: subscribed, so it re-renders when /api/me (or /api/settings) answers after the
 *  first render. Call this, never isInstallAdmin() (or canRename() etc.), in a render: a bare call is read once. */
export function useInstallAdmin(): boolean {
  return useSyncExternalStore(subscribeInstallRole, isInstallAdmin, () => false);
}
