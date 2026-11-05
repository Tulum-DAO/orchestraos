/**
 * AgentRunningJobs — in an agent's slideout, History, under Running: its background jobs in their own block (running
 * ones with their clocks, a recent failure with how it ended and Dismiss; lib/agentJobs.ts trayJobs). While none is
 * there, `fallback` shows instead (the section's own line, "Nothing running beside gm's own turn."). Tapping a job
 * closes the slideout and scrolls the thread to the tool card that launched it (its launch_uuid row).
 */
import { useEffect, useState, type ReactNode } from 'react';
import { failedLabel, jobElapsed, KIND_WORD, trayJobs } from '../../lib/agentJobs';
import { focusWork } from '../../lib/agentCalls';
import { useAgentJobs, useAgentJobsStore } from '../../stores/agentJobs';
import './agentWorking.css';

export function AgentRunningJobs({ agentId, onClose, fallback }: { agentId: string; onClose: () => void; fallback: ReactNode }) {
  const jobs = useAgentJobs(agentId);
  const dismissed = useAgentJobsStore((s) => s.dismissed);
  const [now, setNow] = useState(() => Date.now());
  const shown = trayJobs(jobs, dismissed, now);
  const running = shown.some((j) => j.status === 'running');
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running]);
  if (!shown.length) return <>{fallback}</>;
  const go = (uuid: string) => {
    onClose();
    // after the slideout has gone, so the row is in view of the page and not behind it
    requestAnimationFrame(() => requestAnimationFrame(() => { focusWork(uuid); }));
  };
  return (
    <ul className="agent-running-jobs" data-history-running data-agent-jobs>
      {shown.map((j) => {
        const body = (
          <>
            <span className="agent-running-job-title">{j.title}</span>
            <span className="agent-running-job-kind">{KIND_WORD[j.kind]}</span>
            {j.status === 'running'
              ? <span className="agent-running-job-time">{jobElapsed(j, now)}</span>
              : <span className="agent-running-job-why">{failedLabel(j)}</span>}
          </>
        );
        return (
          <li key={j.id} className="flex items-center gap-1" data-job={j.id} data-status={j.status}>
            {j.launch_uuid
              ? <button type="button" className="agent-running-job" onClick={() => go(j.launch_uuid!)}>{body}</button>
              : <div className="agent-running-job">{body}</div>}
            {j.status === 'failed' && (
              <button type="button" className="agent-running-job-x" aria-label={`Dismiss ${j.title}`}
                      onClick={() => useAgentJobsStore.getState().dismiss(j.id)}>Dismiss</button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
