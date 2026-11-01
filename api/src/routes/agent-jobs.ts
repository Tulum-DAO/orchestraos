/**
 * GET /api/agents/:id/jobs -> {ok, agent, jobs: Job[], source}   (lib/agentJobs.ts)
 *
 * The agent's own background work for its job tray: every running job, then the newest ones that finished in the
 * last 60 minutes (a failed one says why, in `reason`). A call the CLI refused at once is never a job. From the seat's
 * current Claude Code transcript, headless or tmux alike; on a headless seat, a job still running when its CLI last
 * exited is ended there. A runtime other than Claude (or no transcript) has no jobs yet: an empty list, and `source`
 * says why. `:id` is scoped like every /api/agents/:id route.
 *
 * Running jobs are only listed here, never added to the chat. A finished job's result already is in the chat: its
 * <task-notification> line (the finished-work row) or its tool_result under the tool card; `result_uuid` names it.
 */
import { Router, type Request, type Response } from 'express';
import { join } from 'path';
import { agentScopeParam } from '../lib/agent-scope.js';
import { isSafeAgentId } from '../lib/agentPaths.js';
import { dataDir } from '../lib/config.js';
import { getRegistry } from '../services/state-reader.js';
import { headlessSeats } from '../services/status-stream.js';
import { jobsInFile, endWithCli, lastCliExit, trayJobs, type Job } from '../lib/agentJobs.js';
import { resolveTranscriptPath } from './chat-transcript.js';

export interface AgentJobsDeps {
  runtime: (id: string) => string;
  transcriptPath: (id: string) => string | null;
  headless: (id: string) => boolean;
  eventsFile: (id: string) => string;
  /** The clock the tray's 60-minute window reads (tests set it). */
  now?: () => number;
}

export const defaultAgentJobsDeps: AgentJobsDeps = {
  runtime: (id) => String((getRegistry() as any)?.agents?.[id]?.runtime || 'claude').toLowerCase(),
  transcriptPath: (id) => { try { return resolveTranscriptPath(id).path; } catch { return null; } },
  headless: (id) => headlessSeats().has(id),
  eventsFile: (id) => join(dataDir(), 'state', 'headless', id, 'events.jsonl'),
};

export function agentJobs(id: string, deps: AgentJobsDeps = defaultAgentJobsDeps):
    { jobs: Job[]; source: 'transcript' | 'not_claude' | 'no_transcript' } {
  if (deps.runtime(id) !== 'claude') return { jobs: [], source: 'not_claude' };
  const path = deps.transcriptPath(id);
  if (!path) return { jobs: [], source: 'no_transcript' };
  let jobs: Job[];
  try { jobs = jobsInFile(path); } catch { return { jobs: [], source: 'no_transcript' }; }
  if (deps.headless(id)) jobs = endWithCli(jobs, lastCliExit(deps.eventsFile(id)));
  return { jobs: trayJobs(jobs, 20, deps.now ? deps.now() : Date.now()), source: 'transcript' };
}

export function createAgentJobsRouter(deps: AgentJobsDeps = defaultAgentJobsDeps): Router {
  const router = Router();
  router.param('id', agentScopeParam);
  router.get('/:id/jobs', (req: Request, res: Response) => {
    const id = String(req.params.id);
    if (!isSafeAgentId(id)) { res.status(400).json({ ok: false, error: 'bad agent id' }); return; }
    res.json({ ok: true, agent: id, ...agentJobs(id, deps) });
  });
  return router;
}

export default createAgentJobsRouter();
