import { readFileSync, appendFileSync, existsSync } from 'fs';
import { join } from 'path';

const ORCHESTRA_DIR = process.env.ORCHESTRA_DIR || join(process.env.HOME!, 'scripts/agent-orchestra');

function readJsonl(path: string): any[] {
  if (!existsSync(path)) return [];
  return readFileSync(path, 'utf-8').trim().split('\n').filter(Boolean)
    .map(line => { try { return JSON.parse(line); } catch { return null; } }).filter(Boolean);
}

function readJson(path: string, fallback: any = null): any {
  try { return JSON.parse(readFileSync(path, 'utf-8')); } catch { return fallback; }
}

interface Insight {
  ts: string;
  id: string;
  type: 'sequence' | 'routing' | 'time_pattern' | 'frequency_shift';
  description: string;
  suggested_action: string;
  status: 'pending';
  confidence: number;
  evidence_count: number;
  first_observed: string;
}

// Track last run time per user to throttle (max once per 10 min)
const lastRunMap = new Map<string, number>();

export function runDetectors(userId: string): void {
  const now = Date.now();
  const lastRun = lastRunMap.get(userId) || 0;
  if (now - lastRun < 600_000) return; // 10-minute throttle
  lastRunMap.set(userId, now);

  const userDir = join(ORCHESTRA_DIR, 'state', 'users', userId);
  const uiEvents = readJsonl(join(userDir, 'ui-events.jsonl'));
  const unifiedEvents = readJsonl(join(ORCHESTRA_DIR, 'state', 'unified-conversation.jsonl'));
  // Normalize timestamp field: ui-events.jsonl uses `timestamp`, unified uses `ts`.
  // Without this, every UI event failed the `e.ts > cutoff` filters (0 insights since April).
  const allEvents = [...uiEvents, ...unifiedEvents]
    .map(e => (e.ts ? e : { ...e, ts: e.timestamp || '' }))
    .sort((a, b) => (a.ts || '').localeCompare(b.ts || ''));

  const profile = readJson(join(userDir, 'profile.json'), { coaching_rules: [], dismissed_suggestions: [] });
  const existingInsights = readJsonl(join(userDir, 'insights.jsonl'));

  // Collect existing insight IDs and dismissed IDs to avoid duplicates
  const existingIds = new Set<string>(existingInsights.map((i: any) => i.id));
  const dismissedDescs = new Set<string>(
    [...(profile.dismissed_suggestions || []),
     ...existingInsights.filter((i: any) => i.status === 'dismissed').map((i: any) => i.description)]
  );
  const confirmedPatterns = new Set<string>(
    (profile.coaching_rules || []).map((r: any) => r.pattern)
  );

  const newInsights: Insight[] = [];

  // Detector 1: Sequence — agent-to-agent transitions within 5 minutes
  detectSequences(allEvents, existingIds, dismissedDescs, confirmedPatterns, newInsights);

  // Detector 2: Time Pattern — per-agent time-of-day bias
  detectTimePatterns(allEvents, existingIds, dismissedDescs, confirmedPatterns, newInsights);

  // Detector 3: Frequency Shift — week-over-week usage changes
  detectFrequencyShifts(allEvents, existingIds, dismissedDescs, confirmedPatterns, newInsights);

  // Write new insights
  if (newInsights.length > 0) {
    const insightsFile = join(userDir, 'insights.jsonl');
    for (const insight of newInsights) {
      appendFileSync(insightsFile, JSON.stringify(insight) + '\n');
    }
  }
}

function getAgentFromEvent(e: any): string | null {
  return e.agent_id || e.agent_context || null;
}

function detectSequences(
  events: any[], existingIds: Set<string>, dismissed: Set<string>, confirmed: Set<string>, out: Insight[]
) {
  // Look at last 14 days
  const cutoff = new Date(Date.now() - 14 * 86400_000).toISOString();
  const recent = events.filter(e => e.ts > cutoff);

  // Build transition counts: A → B within 5 minutes
  const transitions: Record<string, Record<string, number>> = {};
  const agentOpens: Record<string, number> = {};

  for (let i = 0; i < recent.length - 1; i++) {
    const a = getAgentFromEvent(recent[i]);
    const b = getAgentFromEvent(recent[i + 1]);
    if (!a || !b || a === b) continue;

    const timeDiff = (new Date(recent[i + 1].ts).getTime() - new Date(recent[i].ts).getTime()) / 60_000;
    if (timeDiff > 5) continue;

    if (!transitions[a]) transitions[a] = {};
    transitions[a][b] = (transitions[a][b] || 0) + 1;
    agentOpens[a] = (agentOpens[a] || 0) + 1;
  }

  for (const [from, tos] of Object.entries(transitions)) {
    const total = agentOpens[from] || 1;
    for (const [to, count] of Object.entries(tos)) {
      const pct = count / total;
      if (pct > 0.6 && count >= 5) {
        const desc = `You check ${to} after ${from} ${Math.round(pct * 100)}% of the time`;
        const id = `seq_${from}_${to}`;
        if (existingIds.has(id) || dismissed.has(desc) || confirmed.has(desc)) continue;
        out.push({
          ts: new Date().toISOString(), id, type: 'sequence', description: desc,
          suggested_action: `Auto-open ${to} when you open ${from}`,
          status: 'pending', confidence: Math.min(pct, 0.95), evidence_count: count,
          first_observed: new Date().toISOString().split('T')[0],
        });
      }
    }
  }
}

function detectTimePatterns(
  events: any[], existingIds: Set<string>, dismissed: Set<string>, confirmed: Set<string>, out: Insight[]
) {
  const cutoff = new Date(Date.now() - 14 * 86400_000).toISOString();
  const recent = events.filter(e => e.ts > cutoff);

  const agentBuckets: Record<string, Record<string, number>> = {};

  for (const e of recent) {
    const agent = getAgentFromEvent(e);
    if (!agent) continue;
    const hour = new Date(e.ts).getHours();
    const bucket = hour < 6 ? 'night' : hour < 12 ? 'morning' : hour < 18 ? 'afternoon' : 'evening';
    if (!agentBuckets[agent]) agentBuckets[agent] = {};
    agentBuckets[agent][bucket] = (agentBuckets[agent][bucket] || 0) + 1;
  }

  for (const [agent, buckets] of Object.entries(agentBuckets)) {
    const total = Object.values(buckets).reduce((a, b) => a + b, 0);
    if (total < 5) continue;
    for (const [bucket, count] of Object.entries(buckets)) {
      const pct = count / total;
      if (pct > 0.7) {
        const desc = `You mostly use ${agent} in the ${bucket}`;
        const id = `time_${agent}_${bucket}`;
        if (existingIds.has(id) || dismissed.has(desc) || confirmed.has(desc)) continue;
        out.push({
          ts: new Date().toISOString(), id, type: 'time_pattern', description: desc,
          suggested_action: `Boost ${agent} in your ${bucket} view`,
          status: 'pending', confidence: Math.min(pct, 0.95), evidence_count: count,
          first_observed: new Date().toISOString().split('T')[0],
        });
      }
    }
  }
}

function detectFrequencyShifts(
  events: any[], existingIds: Set<string>, dismissed: Set<string>, confirmed: Set<string>, out: Insight[]
) {
  const oneWeekAgo = new Date(Date.now() - 7 * 86400_000).toISOString();
  const twoWeeksAgo = new Date(Date.now() - 14 * 86400_000).toISOString();

  const thisWeek: Record<string, number> = {};
  const lastWeek: Record<string, number> = {};

  for (const e of events) {
    const agent = getAgentFromEvent(e);
    if (!agent) continue;
    if (e.ts > oneWeekAgo) thisWeek[agent] = (thisWeek[agent] || 0) + 1;
    else if (e.ts > twoWeeksAgo) lastWeek[agent] = (lastWeek[agent] || 0) + 1;
  }

  const allAgents = new Set([...Object.keys(thisWeek), ...Object.keys(lastWeek)]);
  for (const agent of allAgents) {
    const tw = thisWeek[agent] || 0;
    const lw = lastWeek[agent] || 0;
    if (lw === 0 && tw < 3) continue;
    const change = lw > 0 ? (tw - lw) / lw : 1;
    if (Math.abs(change) > 0.5 && (tw + lw) >= 5) {
      const direction = change > 0 ? 'more' : 'less';
      const desc = `You've used ${agent} ${Math.abs(Math.round(change * 100))}% ${direction} this week`;
      const id = `freq_${agent}_${new Date().toISOString().split('T')[0]}`;
      if (existingIds.has(id) || dismissed.has(desc) || confirmed.has(desc)) continue;
      out.push({
        ts: new Date().toISOString(), id, type: 'frequency_shift', description: desc,
        suggested_action: 'Informational — no action needed',
        status: 'pending', confidence: 0.7, evidence_count: tw + lw,
        first_observed: new Date().toISOString().split('T')[0],
      });
    }
  }
}
