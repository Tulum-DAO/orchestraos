/**
 * Who a task is routed to. Only seats THIS install has (its registry.json `agents`): the PM of the
 * task's project or client (`pm-<slug>`) when that seat is registered, else the install's manager
 * (the T0 seat, found by tier: the operator may have called it anything), else nobody. Never a
 * built-in seat name: a fresh install has none of anyone else's seats.
 */
import { readFileSync } from 'fs';
import { join } from 'path';

export type Registry = Record<string, { tier?: string } | null | undefined>;

export function installManager(agents: Registry): string | null {
  for (const [name, row] of Object.entries(agents || {})) {
    if (String(row?.tier || '').toUpperCase() === 'T0') return name;
  }
  return null;
}

export function routeTask(task: { slug?: string | null }, agents: Registry): string | null {
  const pm = task.slug ? `pm-${task.slug}` : null;
  if (pm && agents && agents[pm]) return pm;
  return installManager(agents);
}

/** The install's registered seats (registry.json `agents`), {} when unreadable. */
export function registeredAgents(
  orchestraDir: string = process.env.ORCHESTRA_DIR || join(process.env.HOME || '', 'scripts/agent-orchestra'),
): Registry {
  try {
    return JSON.parse(readFileSync(join(orchestraDir, 'registry.json'), 'utf-8')).agents || {};
  } catch {
    return {};
  }
}
