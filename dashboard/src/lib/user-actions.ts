/**
 * User Action Tracker — logs every UI interaction for audit and UX improvement.
 * Stores in localStorage with automatic rotation (keeps last 1000 actions).
 * Also tracks recent agents for quick-access dropdown.
 */

export interface UserAction {
  ts: number;
  action: string;
  target?: string;
  detail?: string;
}

const ACTIONS_KEY = 'oos_user_actions';
const RECENT_AGENTS_KEY = 'oos_recent_agents';
const MAX_ACTIONS = 1000;
const MAX_RECENT_AGENTS = 10;

export function logAction(action: string, target?: string, detail?: string): void {
  const entry: UserAction = { ts: Date.now(), action, target, detail };
  try {
    const existing = JSON.parse(localStorage.getItem(ACTIONS_KEY) || '[]') as UserAction[];
    existing.push(entry);
    if (existing.length > MAX_ACTIONS) {
      existing.splice(0, existing.length - MAX_ACTIONS);
    }
    localStorage.setItem(ACTIONS_KEY, JSON.stringify(existing));
  } catch {}
}

export function getActions(limit = 100): UserAction[] {
  try {
    const all = JSON.parse(localStorage.getItem(ACTIONS_KEY) || '[]') as UserAction[];
    return all.slice(-limit).reverse();
  } catch {
    return [];
  }
}

export interface RecentAgent {
  id: string;
  name: string;
  tier: string;
  lastAccessed: number;
}

const TWENTY_FOUR_HOURS = 24 * 60 * 60 * 1000;

/** Remove agents not accessed in the last 24 hours */
function pruneStale(agents: RecentAgent[]): RecentAgent[] {
  const cutoff = Date.now() - TWENTY_FOUR_HOURS;
  return agents.filter(a => a.lastAccessed > cutoff);
}

/** Merge two lists: keep the most recent lastAccessed per id, deduplicate, prune, cap at MAX */
function mergeRecents(local: RecentAgent[], server: RecentAgent[]): RecentAgent[] {
  const map = new Map<string, RecentAgent>();
  for (const a of [...server, ...local]) {
    const existing = map.get(a.id);
    if (!existing || a.lastAccessed > existing.lastAccessed) {
      map.set(a.id, a);
    }
  }
  return pruneStale(
    Array.from(map.values()).sort((a, b) => b.lastAccessed - a.lastAccessed)
  ).slice(0, MAX_RECENT_AGENTS);
}

/** Persist to server (fire-and-forget) */
function syncToServer(agents: RecentAgent[]): void {
  fetch('/api/agent-state/recent-agents', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agents }),
  }).catch(() => {});
}

export function trackRecentAgent(id: string, name: string, tier: string): void {
  try {
    const existing = JSON.parse(localStorage.getItem(RECENT_AGENTS_KEY) || '[]') as RecentAgent[];
    const filtered = existing.filter(a => a.id !== id);
    filtered.unshift({ id, name, tier, lastAccessed: Date.now() });
    const pruned = pruneStale(filtered).slice(0, MAX_RECENT_AGENTS);
    localStorage.setItem(RECENT_AGENTS_KEY, JSON.stringify(pruned));
    syncToServer(pruned);
  } catch {}
}

export function getRecentAgents(): RecentAgent[] {
  try {
    const local = JSON.parse(localStorage.getItem(RECENT_AGENTS_KEY) || '[]') as RecentAgent[];
    return pruneStale(local);
  } catch {
    return [];
  }
}

/** Load from server, merge with localStorage, save back to both. Call once on mount. */
export async function loadAndMergeRecentAgents(): Promise<RecentAgent[]> {
  const local = getRecentAgents();
  try {
    const res = await fetch('/api/agent-state/recent-agents');
    if (!res.ok) return local;
    const data = await res.json();
    const server: RecentAgent[] = data.agents || [];
    const merged = mergeRecents(local, server);
    localStorage.setItem(RECENT_AGENTS_KEY, JSON.stringify(merged));
    // Only sync back if we actually added something from server
    if (merged.length !== local.length || merged.some((a, i) => a.id !== local[i]?.id)) {
      syncToServer(merged);
    }
    return merged;
  } catch {
    return local;
  }
}
