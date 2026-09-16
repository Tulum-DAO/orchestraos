import { readFileSync, existsSync, readdirSync } from 'fs';
import { join } from 'path';
import { loadConfig } from '../lib/config.js';

const ORCHESTRA = process.env.ORCHESTRA_DIR || loadConfig().dataDir;

// ── Types ────────────────────────────────────────────────────────────

interface Weights {
  recency: number;
  frequency: number;
  active_context: number;
  time_of_day: number;
  sequence: number;
}

interface UserProfile {
  user_id: string;
  pinned_agents: string[];
  weights: Weights;
  [key: string]: unknown;
}

export interface ScoredAgent {
  agent_id: string;
  name: string;
  tier: string;
  score: number;
  signals: {
    recency: number;
    frequency: number;
    active_context: number;
    time_of_day: number;
    sequence: number;
  };
  pinned: boolean;
}

// ── Defaults ─────────────────────────────────────────────────────────

const DEFAULT_WEIGHTS: Weights = {
  recency: 0.35,
  frequency: 0.25,
  active_context: 0.25,
  time_of_day: 0.10,
  sequence: 0.05,
};

const TIER_ORDER: Record<string, number> = { T0: 0, T1: 1, T2: 2, T3: 3 };

// ── Cache ────────────────────────────────────────────────────────────

let cache: { key: string; ts: number; data: ScoredAgent[] } | null = null;
const CACHE_TTL = 30_000; // 30 seconds

// ── Helpers ──────────────────────────────────────────────────────────

function readJSON<T = unknown>(path: string): T | null {
  try { return JSON.parse(readFileSync(path, 'utf-8')) as T; } catch { return null; }
}

function readJSONL(path: string): Record<string, unknown>[] {
  if (!existsSync(path)) return [];
  try {
    return readFileSync(path, 'utf-8').trim().split('\n')
      .filter(Boolean)
      .map(l => { try { return JSON.parse(l); } catch { return null; } })
      .filter((x): x is Record<string, unknown> => x !== null);
  } catch { return []; }
}

function getTimeBucket(hour: number): string {
  if (hour >= 6 && hour < 12) return 'morning';
  if (hour >= 12 && hour < 17) return 'afternoon';
  if (hour >= 17 && hour < 22) return 'evening';
  return 'night';
}

// ── Signal Calculators ───────────────────────────────────────────────

function computeRecency(agentId: string, events: Record<string, unknown>[], conversations: Record<string, unknown>[]): number {
  // Find most recent interaction with this agent across both sources
  let lastTs = 0;

  for (const ev of events) {
    if (ev.agent_id === agentId || ev.agent === agentId) {
      const ts = new Date(ev.timestamp as string).getTime();
      if (ts > lastTs) lastTs = ts;
    }
  }

  for (const msg of conversations) {
    if (msg.agent_id === agentId || msg.agent === agentId) {
      const ts = new Date((msg.timestamp || msg.created_at) as string).getTime();
      if (ts > lastTs) lastTs = ts;
    }
  }

  if (lastTs === 0) return 0.05;

  const minutesAgo = (Date.now() - lastTs) / 60_000;
  if (minutesAgo < 10) return 1.0;
  if (minutesAgo < 60) return 0.8;
  if (minutesAgo < 360) return 0.5;
  if (minutesAgo < 1440) return 0.2;
  return 0.05;
}

function computeFrequency(agentId: string, events: Record<string, unknown>[], conversations: Record<string, unknown>[], maxCount: number): number {
  if (maxCount === 0) return 0;

  const sevenDaysAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
  let count = 0;

  for (const ev of events) {
    if ((ev.agent_id === agentId || ev.agent === agentId)) {
      const ts = new Date(ev.timestamp as string).getTime();
      if (ts >= sevenDaysAgo) count++;
    }
  }

  for (const msg of conversations) {
    if ((msg.agent_id === agentId || msg.agent === agentId)) {
      const ts = new Date((msg.timestamp || msg.created_at) as string).getTime();
      if (ts >= sevenDaysAgo) count++;
    }
  }

  return count / maxCount;
}

function computeActiveContext(agentId: string): number {
  // Check heartbeat / state file for agent
  const statePath = join(ORCHESTRA, 'state', `${agentId}.json`);
  const state = readJSON<Record<string, unknown>>(statePath);
  if (!state) return 0;

  const status = (state.status || state.state || '') as string;
  const lastUpdated = (state.last_updated || state.updated_at || state.timestamp) as string | undefined;

  let score = 0;

  // Status-based scoring
  const lowerStatus = status.toLowerCase();
  if (lowerStatus === 'working' || lowerStatus === 'active') {
    score = 0.5;
  }
  if (lowerStatus === 'blocked' || lowerStatus === 'needs_shaw' || lowerStatus === 'waiting_on_shaw') {
    score = 1.0;
  }

  // Recency boost: if updated in last 30 minutes, add 0.3
  if (lastUpdated) {
    const updatedAgo = (Date.now() - new Date(lastUpdated).getTime()) / 60_000;
    if (updatedAgo < 30) {
      if (score < 1.0) {
        score = Math.min(1.0, score + (lowerStatus === 'working' || lowerStatus === 'active' ? 0.5 : 0.3));
      }
    }
  }

  return Math.min(1.0, score);
}

function computeTimeOfDay(agentId: string, events: Record<string, unknown>[], conversations: Record<string, unknown>[]): number {
  const currentBucket = getTimeBucket(new Date().getHours());
  const fourteenDaysAgo = Date.now() - 14 * 24 * 60 * 60 * 1000;

  let totalInteractions = 0;
  let bucketInteractions = 0;

  const countEvent = (ev: Record<string, unknown>) => {
    if (ev.agent_id !== agentId && ev.agent !== agentId) return;
    const ts = new Date((ev.timestamp || ev.created_at) as string);
    if (ts.getTime() < fourteenDaysAgo) return;
    totalInteractions++;
    if (getTimeBucket(ts.getHours()) === currentBucket) bucketInteractions++;
  };

  events.forEach(countEvent);
  conversations.forEach(countEvent);

  if (totalInteractions === 0) return 0.25; // uniform default (1/4 buckets)
  return bucketInteractions / totalInteractions;
}

function computeSequence(): number {
  // Placeholder — will be enriched later with sequential pattern detection
  return 0;
}

// ── 7-day Frequency Counts (pre-computed for normalization) ──────────

function getAgentFrequencyCounts(
  agentIds: string[],
  events: Record<string, unknown>[],
  conversations: Record<string, unknown>[]
): Map<string, number> {
  const sevenDaysAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const counts = new Map<string, number>();

  for (const id of agentIds) counts.set(id, 0);

  for (const ev of events) {
    const id = (ev.agent_id || ev.agent) as string;
    if (id && counts.has(id)) {
      const ts = new Date(ev.timestamp as string).getTime();
      if (ts >= sevenDaysAgo) counts.set(id, (counts.get(id) || 0) + 1);
    }
  }

  for (const msg of conversations) {
    const id = (msg.agent_id || msg.agent) as string;
    if (id && counts.has(id)) {
      const ts = new Date((msg.timestamp || msg.created_at) as string).getTime();
      if (ts >= sevenDaysAgo) counts.set(id, (counts.get(id) || 0) + 1);
    }
  }

  return counts;
}

// ── Main Scoring Function ────────────────────────────────────────────

export function scoreAgents(userId: string): ScoredAgent[] {
  // Check cache
  const cacheKey = userId;
  if (cache && cache.key === cacheKey && (Date.now() - cache.ts) < CACHE_TTL) {
    return cache.data;
  }

  // Load registry
  const registry = readJSON<{ agents: Record<string, { tier: string; name: string; [k: string]: unknown }> }>(
    join(ORCHESTRA, 'registry.json')
  );
  if (!registry || !registry.agents) return [];

  const agentIds = Object.keys(registry.agents);

  // Load user profile for weights and pinned agents
  const profilePath = join(ORCHESTRA, 'state', 'users', userId, 'profile.json');
  const profile = readJSON<UserProfile>(profilePath);
  const weights: Weights = profile?.weights || DEFAULT_WEIGHTS;
  const pinnedAgents = new Set(profile?.pinned_agents || []);

  // Load event sources
  const uiEventsPath = join(ORCHESTRA, 'state', 'users', userId, 'ui-events.jsonl');
  const conversationsPath = join(ORCHESTRA, 'state', 'unified-conversation.jsonl');
  const uiEvents = readJSONL(uiEventsPath);
  const conversations = readJSONL(conversationsPath);

  // Pre-compute frequency counts for normalization
  const freqCounts = getAgentFrequencyCounts(agentIds, uiEvents, conversations);
  const maxFreq = Math.max(...freqCounts.values(), 0);

  // Score each agent
  const scored: ScoredAgent[] = agentIds.map(agentId => {
    const agentDef = registry.agents[agentId];
    const signals = {
      recency: computeRecency(agentId, uiEvents, conversations),
      frequency: computeFrequency(agentId, uiEvents, conversations, maxFreq),
      active_context: computeActiveContext(agentId),
      time_of_day: computeTimeOfDay(agentId, uiEvents, conversations),
      sequence: computeSequence(),
    };

    const score =
      signals.recency * weights.recency +
      signals.frequency * weights.frequency +
      signals.active_context * weights.active_context +
      signals.time_of_day * weights.time_of_day +
      signals.sequence * weights.sequence;

    return {
      agent_id: agentId,
      name: agentDef.name as string,
      tier: (agentDef.tier || 'T3') as string,
      score: Math.round(score * 1000) / 1000, // 3 decimal places
      signals,
      pinned: pinnedAgents.has(agentId),
    };
  });

  // Sort: descending by score, tie-break by tier (T0 > T1 > T2 > T3)
  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return (TIER_ORDER[a.tier] ?? 9) - (TIER_ORDER[b.tier] ?? 9);
  });

  // Pinned agents override position — move them to the top while preserving their order
  const pinned = scored.filter(a => a.pinned);
  const unpinned = scored.filter(a => !a.pinned);
  const result = [...pinned, ...unpinned];

  // Update cache
  cache = { key: cacheKey, ts: Date.now(), data: result };

  return result;
}
