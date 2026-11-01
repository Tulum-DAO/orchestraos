/**
 * agentJobs (store) — an agent's background jobs (lib/agentJobs.ts), polled while its page is open, for the two places
 * that show them: the thread's tool cards (the launching card's live status) and the slideout's History, under Running
 * (with the failures you dismissed there).
 * One poll per page (usePollAgentJobs, mounted by the agent page); everyone else reads.
 */
import { useEffect } from 'react';
import { create } from 'zustand';
import { fetchAgentJobs, type AgentJob } from '../lib/agentJobs';

const POLL_RUNNING_MS = 2000;
const POLL_IDLE_MS = 8000;
/** The route is not there yet (404): look again now and then. */
const POLL_ABSENT_MS = 30000;

interface Store {
  byAgent: Record<string, AgentJob[] | undefined>;
  /** Failures dismissed in the History, by job id (for this page load). */
  dismissed: ReadonlySet<string>;
  set: (id: string, jobs: AgentJob[]) => void;
  dismiss: (jobId: string) => void;
}
export const useAgentJobsStore = create<Store>((set) => ({
  byAgent: {},
  dismissed: new Set(),
  set: (id, jobs) => set((s) => ({ byAgent: { ...s.byAgent, [id]: jobs } })),
  dismiss: (jobId) => set((s) => ({ dismissed: new Set(s.dismissed).add(jobId) })),
}));

const NONE: AgentJob[] = [];
/** This agent's jobs as last read (none before the first answer, or when the route is not there). */
export function useAgentJobs(agentId: string | undefined): AgentJob[] {
  return useAgentJobsStore((s) => (agentId ? s.byAgent[agentId] : undefined) ?? NONE);
}

/** Poll this agent's jobs while mounted: every 2 s while one runs, else every 8 s; a 404 is "none", asked again in 30 s. */
export function usePollAgentJobs(agentId: string): void {
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let last: AgentJob[] = [];
    const poll = async () => {
      const res = await fetchAgentJobs(agentId);
      if (!alive) return;
      if (res === 'none') { last = []; useAgentJobsStore.getState().set(agentId, NONE); timer = setTimeout(poll, POLL_ABSENT_MS); return; }
      if (res) { last = res; useAgentJobsStore.getState().set(agentId, res); }
      timer = setTimeout(poll, last.some((j) => j.status === 'running') ? POLL_RUNNING_MS : POLL_IDLE_MS);
    };
    void poll();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [agentId]);
}
