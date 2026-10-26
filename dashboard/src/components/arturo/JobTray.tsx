/**
 * JobTray — Arturo's background work, docked between the last message and the composer.
 *
 * Background jobs outlive the turn that started them, so they don't belong in the message list: they would be
 * buried under newer messages. Collapsed, the tray is one line ("2 running · Reading the post · 0:42");
 * expanded, one row per job with its live steps (the same tool cards as the chat) and a cancel button. A job
 * that failed stays until dismissed. When a job ends its result is already posted into the thread
 * server-side: `onEnded` lets the page reload the thread to show it.
 */
import { useEffect, useRef, useState } from 'react';
import { ChevronDown, LoaderCircle, X } from 'lucide-react';
import ToolRun from './ToolRun';
import { cancelJob, elapsed, fetchJobs, jobToolParts, newlyEnded, trayHeader, type ArturoJob } from '../../lib/arturoJobs';

const POLL_RUNNING_MS = 1500;
const POLL_IDLE_MS = 6000;

export default function JobTray({ threadId, onEnded, kick = 0 }: {
  threadId: string;
  /** Jobs just ended: their results are in the thread now. */
  onEnded?: (jobIds: string[]) => void;
  /** Bump after a turn completes: a job it started shows at once instead of on the next idle poll. */
  kick?: number;
}) {
  const [jobs, setJobs] = useState<ArturoJob[]>([]);
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [, setTick] = useState(0);
  const prev = useRef<ArturoJob[]>([]);
  const endedRef = useRef(onEnded);
  endedRef.current = onEnded;

  useEffect(() => {
    if (!threadId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    prev.current = [];
    const poll = async () => {
      const got = await fetchJobs(threadId);
      if (!alive) return;
      if (got) {
        const ended = newlyEnded(prev.current, got);
        prev.current = got;
        setJobs(got);
        if (ended.length) endedRef.current?.(ended);
      }
      const running = (got || prev.current).some((j) => j.status === 'running');
      timer = setTimeout(poll, running ? POLL_RUNNING_MS : POLL_IDLE_MS);
    };
    void poll();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [threadId, kick]);

  const running = jobs.some((j) => j.status === 'running');
  useEffect(() => {                       // the elapsed clock ticks while something runs
    if (!running) return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [running]);

  // Shown: running jobs, and failures not yet dismissed. A job that finished fine is in the thread now.
  const shown = jobs.filter((j) => j.status === 'running'
    || ((j.status === 'failed' || j.status === 'lost') && !dismissed.has(j.id)));
  if (shown.length === 0) return null;

  return (
    <div className="arturo-jobtray w-full rounded-control border border-line bg-field px-2.5 py-1.5 mb-1.5"
         data-testid="job-tray">
      <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)}
              className="flex w-full items-center gap-1.5 text-left text-[12.5px] text-ink-2">
        {running ? <LoaderCircle size={13} className="shrink-0 animate-spin text-ink-3" aria-label="working" />
                 : <X size={13} className="shrink-0 text-red" aria-label="didn't finish" />}
        <span className="min-w-0 flex-1 truncate tabular-nums">{trayHeader(shown)}</span>
        <ChevronDown size={12} strokeWidth={2.2} className="shrink-0 transition-transform duration-200"
                     style={{ transform: open ? 'rotate(0deg)' : 'rotate(-90deg)' }} />
      </button>
      {open && (
        <div className="mt-1.5 flex max-h-64 flex-col gap-2 overflow-y-auto">
          {shown.map((j) => (
            <div key={j.id} className="flex flex-col">
              <div className="flex items-center gap-2 text-[12.5px]">
                <span className="min-w-0 flex-1 truncate font-medium text-ink">{j.title}</span>
                <span className="shrink-0 tabular-nums text-ink-3">
                  {j.status === 'running' ? elapsed(j) : j.status === 'lost' ? 'lost' : 'failed'}
                </span>
                {j.status === 'running' ? (
                  <button type="button" className="shrink-0 text-ink-3 hover:text-red" aria-label={`Cancel ${j.title}`}
                          onClick={async () => { if (await cancelJob(threadId, j.id)) setJobs((cur) => cur.filter((x) => x.id !== j.id)); }}>
                    Cancel
                  </button>
                ) : (
                  <button type="button" className="shrink-0 text-ink-3" aria-label={`Dismiss ${j.title}`}
                          onClick={() => setDismissed((d) => new Set(d).add(j.id))}>Dismiss</button>
                )}
              </div>
              {j.error && j.status !== 'running' && <span className="text-[11.5px] text-red">{j.error}</span>}
              {j.steps.length > 0 && <ToolRun tools={jobToolParts(j)} />}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
