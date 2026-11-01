/**
 * AgentJobTray — under the working row, the agent's own live background work (lib/agentJobs.ts): one chip per
 * background command, subagent or wait, with how long it has run. Running work lives here and never in the thread;
 * when a job finishes its result card is in the thread where it finished, so the chip goes. A failed job stays
 * until dismissed and opens its result row when it has one. Before the jobs route exists (404) nothing is shown.
 *
 * Each chip is its own list item so a subagent's chip can later expand in place into its steps.
 */
import { useEffect, useState } from 'react';
import { Bot, Hourglass, SquareTerminal, X } from 'lucide-react';
import { failedLabel, fetchAgentJobs, jobElapsed, KIND_WORD, trayJobs, type AgentJob, type AgentJobKind } from '../../lib/agentJobs';
import { focusWork } from '../../lib/agentCalls';
import './agentWorking.css';

const POLL_RUNNING_MS = 2000;
const POLL_IDLE_MS = 8000;
/** The route is not there yet: look again now and then, so the tray appears once it is. */
const POLL_ABSENT_MS = 30000;

const ICON: Record<AgentJobKind, typeof Bot> = { command: SquareTerminal, subagent: Bot, wait: Hourglass };

export default function AgentJobTray({ agentId }: { agentId: string }) {
  // Kept with the seat they belong to, so another seat's page never shows them for a moment.
  const [got, setGot] = useState<{ agentId: string; jobs: AgentJob[] }>({ agentId, jobs: [] });
  const jobs = got.agentId === agentId ? got.jobs : [];
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let last: AgentJob[] = [];
    const poll = async () => {
      const res = await fetchAgentJobs(agentId);
      if (!alive) return;
      if (res === 'none') { last = []; setGot({ agentId, jobs: [] }); timer = setTimeout(poll, POLL_ABSENT_MS); return; }
      // each answer also moves the clock on, so a failure older than the hour leaves even with nothing running
      if (res) { last = res; setGot({ agentId, jobs: res }); setNow(Date.now()); }
      timer = setTimeout(poll, last.some((j) => j.status === 'running') ? POLL_RUNNING_MS : POLL_IDLE_MS);
    };
    void poll();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [agentId]);

  const shown = trayJobs(jobs, dismissed, now);
  const running = shown.some((j) => j.status === 'running');
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running]);

  if (shown.length === 0) return null;
  return (
    <ul className="agent-jobs" data-testid="agent-job-tray" aria-label="Background work" data-arturo-avoid>
      {shown.map((j) => {
        const Icon = ICON[j.kind];
        return (
          <li key={j.id} className="agent-job" data-job-kind={j.kind} data-status={j.status} title={j.title}>
            {/* Its kind by icon; running by its ticking clock, failed in red. */}
            <Icon size={12} className={`shrink-0 ${j.status === 'failed' ? 'text-red' : 'text-ink-3'}`} aria-label={KIND_WORD[j.kind]} />
            <span className="agent-job-title">{j.title}</span>
            <span className="agent-job-time">{j.status === 'running' ? jobElapsed(j, now) : failedLabel(j)}</span>
            {j.status === 'failed' && j.result_uuid && (
              <button type="button" className="agent-job-open" onClick={() => focusWork(j.result_uuid!)}>See why</button>
            )}
            {j.status === 'failed' && (
              <button type="button" className="agent-job-x" aria-label={`Dismiss ${j.title}`}
                      onClick={() => setDismissed((d) => new Set(d).add(j.id))}><X size={11} /></button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
