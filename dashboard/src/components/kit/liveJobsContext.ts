// An agent's background jobs by the tool call that launched them (lib/agentJobs.ts jobsByToolUse), for its thread's
// tool cards: a launching card shows the job's live status. Absent outside an agent's thread (Arturo's page, his
// pane), where cards are as they were.
import { createContext } from 'react';
import type { AgentJob } from '../../lib/agentJobs';

export const LiveJobsContext = createContext<Map<string, AgentJob> | null>(null);
