/**
 * agentJobs.ts — an agent's own live background work, for the tray under its working row (components/agent/
 * AgentJobTray.tsx): background commands, subagents (one chip each) and waits.
 *
 *   GET /api/agents/:id/jobs → {ok, jobs:[{id, kind:"command"|"subagent"|"wait", title, status:"running"|"done"|"failed",
 *                                         started_at, ended_at?, result_uuid?}]}
 *
 * A 404 means the route is not there yet: no tray, nothing shown. Running work stays in the tray and out of the
 * thread; a finished job's result card is already in the thread where it finished (lib/threadKit/results.ts), so a
 * done job leaves the tray. A failed one stays until dismissed, and opens its result row when it has one.
 */
export type AgentJobKind = 'command' | 'subagent' | 'wait';
export type AgentJobStatus = 'running' | 'done' | 'failed';

export interface AgentJob {
  id: string;
  kind: AgentJobKind;
  title: string;
  status: AgentJobStatus;
  started_at: string;
  ended_at?: string;
  result_uuid?: string;
}

const KINDS = new Set<AgentJobKind>(['command', 'subagent', 'wait']);
const STATUSES = new Set<AgentJobStatus>(['running', 'done', 'failed']);

/** 'none' when the route is not there yet (404), null on any other failure (keep what is shown). */
export async function fetchAgentJobs(agentId: string, fetchFn: typeof fetch = fetch): Promise<AgentJob[] | 'none' | null> {
  try {
    const r = await fetchFn(`/api/agents/${encodeURIComponent(agentId)}/jobs`);
    if (r.status === 404) return 'none';
    if (!r.ok) return null;
    const d = await r.json().catch(() => null) as { ok?: boolean; jobs?: unknown } | null;
    if (!d?.ok || !Array.isArray(d.jobs)) return null;
    return d.jobs.flatMap((raw) => {
      const j = raw as Partial<AgentJob>;
      if (!j || typeof j.id !== 'string' || !KINDS.has(j.kind as AgentJobKind) || !STATUSES.has(j.status as AgentJobStatus)) return [];
      return [{
        id: j.id, kind: j.kind as AgentJobKind, status: j.status as AgentJobStatus,
        title: typeof j.title === 'string' && j.title.trim() ? j.title.trim() : KIND_WORD[j.kind as AgentJobKind],
        started_at: String(j.started_at ?? ''),
        ...(typeof j.ended_at === 'string' ? { ended_at: j.ended_at } : {}),
        ...(typeof j.result_uuid === 'string' && j.result_uuid ? { result_uuid: j.result_uuid } : {}),
      }];
    });
  } catch {
    return null;
  }
}

export const KIND_WORD: Record<AgentJobKind, string> = { command: 'Background command', subagent: 'Subagent', wait: 'Waiting' };

/** What the tray shows: running work (oldest first), then failures not yet dismissed. Done work is in the thread. */
export function trayJobs(jobs: AgentJob[], dismissed: ReadonlySet<string> = new Set()): AgentJob[] {
  const t = (j: AgentJob) => Date.parse(j.started_at) || 0;
  const running = jobs.filter((j) => j.status === 'running').sort((a, b) => t(a) - t(b));
  const failed = jobs.filter((j) => j.status === 'failed' && !dismissed.has(j.id));
  return [...running, ...failed];
}

/** How long, "0:42" / "12:05", from its start to its end (or now). Empty when the start is unreadable. */
export function jobElapsed(j: AgentJob, now = Date.now()): string {
  const start = Date.parse(j.started_at);
  if (!Number.isFinite(start)) return '';
  const end = j.ended_at ? Date.parse(j.ended_at) : now;
  const s = Math.max(0, Math.round(((Number.isFinite(end) ? end : now) - start) / 1000));
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}:${String(s % 60).padStart(2, '0')}` : `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}
