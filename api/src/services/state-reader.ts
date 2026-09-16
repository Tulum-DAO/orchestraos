import fs from 'fs';
import path from 'path';
import { loadConfig } from '../lib/config.js';

const ORCHESTRA_DIR = process.env.ORCHESTRA_DIR || loadConfig().dataDir;
const OMNI_DIR = process.env.OMNI_DIR || path.join(ORCHESTRA_DIR, 'facts');
const DASHBOARD_V4_DIR = process.env.DASHBOARD_V4_DIR || path.join(ORCHESTRA_DIR, 'dashboard_v4');

// Excluded state files that are not per-agent state
const STATE_EXCLUDES = new Set([
  'gm-session.json',
  'rate-limit-state.json',
  'dispatcher.pid',
  'mac-heartbeat.json',
  'metrics-collector.json',
  'voice-agents.json',
]);

function readJsonSafe<T = unknown>(filePath: string): T | null {
  try {
    const raw = fs.readFileSync(filePath, 'utf-8');
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function readTextSafe(filePath: string): string | null {
  try {
    return fs.readFileSync(filePath, 'utf-8');
  } catch {
    return null;
  }
}

function listJsonFiles(dir: string): string[] {
  try {
    return fs.readdirSync(dir).filter((f) => f.endsWith('.json'));
  } catch {
    return [];
  }
}

function listDirs(dir: string): string[] {
  try {
    return fs
      .readdirSync(dir, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => d.name);
  } catch {
    return [];
  }
}

// ── Registry ──────────────────────────────────────────────────────────
export function getRegistry(): Record<string, unknown> | null {
  return readJsonSafe(path.join(ORCHESTRA_DIR, 'registry.json'));
}

// ── Agent States ──────────────────────────────────────────────────────
export function getAllAgentStates(): Record<string, Record<string, unknown>> {
  const dir = path.join(ORCHESTRA_DIR, 'state');
  const files = listJsonFiles(dir).filter((f) => !STATE_EXCLUDES.has(f));
  const states: Record<string, Record<string, unknown>> = {};
  for (const f of files) {
    const id = f.replace('.json', '');
    const data = readJsonSafe<Record<string, unknown>>(path.join(dir, f));
    if (data) states[id] = data;
  }
  // Overlay state/agents/*.json (authoritative per-agent state)
  const agentsDir = path.join(ORCHESTRA_DIR, 'state', 'agents');
  const agentFiles = listJsonFiles(agentsDir);
  for (const f of agentFiles) {
    const id = f.replace('.json', '');
    const data = readJsonSafe<Record<string, unknown>>(path.join(agentsDir, f));
    if (data) states[id] = { ...(states[id] || {}), ...data };
  }
  return states;
}

// ── Tasks ─────────────────────────────────────────────────────────────
export function getAllTasks(): Record<string, unknown>[] {
  const dir = path.join(ORCHESTRA_DIR, 'tasks');
  const files = listJsonFiles(dir);
  const tasks: Record<string, unknown>[] = [];
  for (const f of files) {
    const data = readJsonSafe<Record<string, unknown>>(path.join(dir, f));
    if (data) {
      data._filename = f;
      tasks.push(data);
    }
  }
  // Sort newest first by created_at or filename
  tasks.sort((a, b) => {
    const da = (a.created_at as string) || (a._filename as string) || '';
    const db = (b.created_at as string) || (b._filename as string) || '';
    return db.localeCompare(da);
  });
  return tasks;
}

// ── Inbox Counts ──────────────────────────────────────────────────────
export function getInboxCounts(): Record<string, number> {
  const inboxDir = path.join(ORCHESTRA_DIR, 'queue', 'inbox');
  const counts: Record<string, number> = {};
  const agents = listDirs(inboxDir);
  for (const agent of agents) {
    try {
      const files = fs.readdirSync(path.join(inboxDir, agent));
      counts[agent] = files.length;
    } catch {
      counts[agent] = 0;
    }
  }
  return counts;
}

// ── Pending Context ───────────────────────────────────────────────────
export function getPendingContext(): Record<string, unknown>[] {
  const dir = path.join(ORCHESTRA_DIR, 'pending_context');
  return listJsonFiles(dir).map((f) => {
    const data = readJsonSafe<Record<string, unknown>>(path.join(dir, f));
    return data ? { ...data, _filename: f } : { _filename: f };
  });
}

// ── Completed Reports ─────────────────────────────────────────────────
export function getCompletedReports(): Record<string, unknown>[] {
  const dir = path.join(ORCHESTRA_DIR, 'completed_reports');
  return listJsonFiles(dir).map((f) => {
    const data = readJsonSafe<Record<string, unknown>>(path.join(dir, f));
    return data ? { ...data, _filename: f } : { _filename: f };
  });
}

// ── Omni-Context ──────────────────────────────────────────────────────
export function getContextLayer(): Record<string, unknown> | null {
  return readJsonSafe(path.join(OMNI_DIR, 'global', 'context_layer.json'));
}

export function getGlobalFacts(): Record<string, unknown> | null {
  return readJsonSafe(path.join(OMNI_DIR, 'global', 'facts_db.json'));
}

export function getProjectFacts(project: string): Record<string, unknown> | null {
  // Sanitize project name to prevent path traversal
  const safe = path.basename(project);
  return readJsonSafe(path.join(OMNI_DIR, 'projects', safe, 'facts_db.json'));
}

export function getAllProjects(): string[] {
  return listDirs(path.join(OMNI_DIR, 'projects'));
}

export function getHandoff(project: string): string | null {
  const safe = path.basename(project);
  return readTextSafe(path.join(OMNI_DIR, 'projects', safe, 'handoff.md'));
}

export function getAllHandoffs(): Record<string, string | null> {
  const projects = getAllProjects();
  const handoffs: Record<string, string | null> = {};
  for (const p of projects) {
    handoffs[p] = getHandoff(p);
  }
  return handoffs;
}

// ── Dashboard / Roadmaps ──────────────────────────────────────────────
export function getRoadmaps(): Record<string, unknown> | null {
  // Try ORCHESTRA_DIR/state/roadmaps.json first, fall back to DASHBOARD_V4_DIR
  return readJsonSafe(path.join(ORCHESTRA_DIR, 'state', 'roadmaps.json'))
    || readJsonSafe(path.join(DASHBOARD_V4_DIR, 'roadmaps.json'));
}

// ── Special State Files ───────────────────────────────────────────────
export function getMacHeartbeat(): Record<string, unknown> | null {
  return readJsonSafe(path.join(ORCHESTRA_DIR, 'state', 'mac-heartbeat.json'));
}

export function getShawPresence(): Record<string, unknown> | null {
  const data = readJsonSafe<Record<string, unknown>>(path.join(ORCHESTRA_DIR, 'state', 'operator-presence.json'));
  if (!data || !data.last_telegram_message) return data;

  // Compute derived status based on age
  try {
    const lastMsg = new Date(data.last_telegram_message as string).getTime();
    const ageMin = (Date.now() - lastMsg) / 60000;

    // Check Mac heartbeat for transit detection
    const hb = readJsonSafe<Record<string, unknown>>(path.join(ORCHESTRA_DIR, 'state', 'mac-heartbeat.json'));
    const macDead = hb && (hb.consecutive_failures as number) >= 3;

    let status: string;
    if (macDead) {
      status = 'transit';
    } else if (ageMin < 5) {
      status = 'active';
    } else if (ageMin < 30) {
      status = 'away';
    } else {
      status = 'offline';
    }

    return { ...data, status, minutes_ago: Math.round(ageMin) };
  } catch {
    return data;
  }
}

export function getRateLimitState(): Record<string, unknown> | null {
  return readJsonSafe(path.join(ORCHESTRA_DIR, 'state', 'rate-limit-state.json'));
}

export function getGMSession(): Record<string, unknown> | null {
  return readJsonSafe(path.join(ORCHESTRA_DIR, 'state', 'gm-session.json'));
}

export function isDispatcherRunning(): boolean {
  const pidFile = path.join(ORCHESTRA_DIR, 'state', 'dispatcher.pid');
  const raw = readTextSafe(pidFile);
  if (!raw) return false;
  const pid = parseInt(raw.trim(), 10);
  if (isNaN(pid)) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

// ── Voice ─────────────────────────────────────────────────────────────
export function getVoiceAgentState(): Record<string, unknown> | null {
  return readJsonSafe(path.join(ORCHESTRA_DIR, 'state', 'voice-agents.json'));
}

export function getVoiceTranscripts(limit = 20): Record<string, unknown>[] {
  const dir = path.join(ORCHESTRA_DIR, 'voice-transcripts');
  const files = listJsonFiles(dir);
  // Sort newest first by filename
  files.sort((a, b) => b.localeCompare(a));
  const selected = files.slice(0, limit);
  return selected
    .map((f) => {
      const data = readJsonSafe<Record<string, unknown>>(path.join(dir, f));
      return data ? { ...data, _filename: f } : null;
    })
    .filter(Boolean) as Record<string, unknown>[];
}

// ── Projects ─────────────────────────────────────────────────────────
export function getProjectsKnowledge(): Record<string, any> | null {
  return readJsonSafe(path.join(ORCHESTRA_DIR, 'state', 'knowledge', 'projects.json'));
}

// ── Client Ecosystems ────────────────────────────────────────────────
export function getClientEcosystem(clientId: string) {
  const safe = path.basename(clientId);
  const ecosystemDir = path.join(ORCHESTRA_DIR, 'state', 'client-ecosystems');
  return readJsonSafe(path.join(ecosystemDir, `${safe}.json`));
}

export function getAllClientEcosystems() {
  const dir = path.join(ORCHESTRA_DIR, 'state', 'client-ecosystems');
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir)
    .filter(f => f.endsWith('.json'))
    .map(f => readJsonSafe(path.join(dir, f)))
    .filter(Boolean);
}

// ── Agent Scan Results (from instance monitor) ──────────────────────
export function getAgentScanResults(): Record<string, unknown> | null {
  const paths = [
    path.join(ORCHESTRA_DIR, 'state', 'wake-scan-results.json'),
    path.join(ORCHESTRA_DIR, 'state', 'mac-agent-state.json'),
    path.join(ORCHESTRA_DIR, 'wake-scan-results.json'),
    // Instance monitor may write outside orchestra dir
    path.join(path.dirname(ORCHESTRA_DIR), 'mac-agent-state.json'),
  ];
  for (const p of paths) {
    const data = readJsonSafe(p);
    if (data) return data as Record<string, unknown>;
  }
  return null;
}

// ── Activity Log ──────────────────────────────────────────────────────
export function getRecentActivity(limit = 50): Record<string, unknown>[] {
  const filePath = path.join(ORCHESTRA_DIR, 'activity.jsonl');
  const raw = readTextSafe(filePath);
  if (!raw) return [];
  const lines = raw.trim().split('\n').filter(Boolean);
  // Take last N lines
  const recent = lines.slice(-limit);
  const events: Record<string, unknown>[] = [];
  for (const line of recent) {
    try {
      events.push(JSON.parse(line));
    } catch {
      // skip malformed lines
    }
  }
  // Return newest first
  events.reverse();
  return events;
}
