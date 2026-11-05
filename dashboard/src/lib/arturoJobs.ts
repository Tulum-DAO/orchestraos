/**
 * arturoJobs.ts — Arturo's background jobs in a thread (services/arturo/jobs.py): what the job tray shows.
 *
 * Work that outlives the turn that asked for it (web research, a question for the GM) runs as a job. The tray
 * above the composer lists running jobs with their live steps; when a job ends, its result is posted into the
 * thread server-side, so the page reloads the thread to show it.
 */
import type { ToolPart, TurnPart } from './turnParts.ts';

export interface JobStep { call_id: string | null; name: string; args_summary: string; ok: boolean | null; summary: string }
export interface ArturoJob {
  id: string; thread: string; kind: string; title: string;
  status: 'running' | 'done' | 'failed' | 'cancelled' | 'lost';
  started: number; ended: number | null; steps: JobStep[]; result: string | null; error: string | null;
}

export async function fetchJobs(threadId: string): Promise<ArturoJob[] | null> {
  try {
    const res = await fetch(`/api/arturo/threads/${encodeURIComponent(threadId)}?jobs=1`);
    if (!res.ok) return null;
    const json = await res.json();
    return Array.isArray(json.jobs) ? json.jobs : [];
  } catch { return null; }
}

export async function cancelJob(threadId: string, jobId: string): Promise<boolean> {
  try {
    const res = await fetch(`/api/arturo/threads/${encodeURIComponent(threadId)}/jobs/${encodeURIComponent(jobId)}/cancel`,
      { method: 'POST' });
    return res.ok;
  } catch { return false; }
}

/** A finished job's message in the thread: its steps (the card that was pinned in the tray), then its text.
 *  Undefined for any other message, or a job that ran no steps. */
export function jobResultParts(meta: { kind?: string; steps?: JobStep[]; job_id?: string } | undefined,
  text: string): TurnPart[] | undefined {
  if (meta?.kind !== 'job_result' || !meta.steps?.length) return undefined;
  const tools = jobToolParts({ id: meta.job_id || 'job', status: 'done', steps: meta.steps } as ArturoJob);
  return [...tools, { kind: 'text', text }];
}

/** A job's steps as tool-card rows (the shared card, components/kit/ToolCard.tsx). */
export function jobToolParts(job: ArturoJob): ToolPart[] {
  return job.steps.map((s, i) => ({
    kind: 'tool', callId: s.call_id || `${job.id}-${i}`, name: s.name, argsSummary: s.args_summary,
    status: s.ok === null ? (job.status === 'running' ? 'running' : 'failed') : s.ok ? 'ok' : 'failed',
    summary: s.summary,
  }));
}

export function elapsed(job: ArturoJob, now = Date.now() / 1000): string {
  const s = Math.max(0, Math.round((job.ended ?? now) - job.started));
  return s < 60 ? `0:${String(s).padStart(2, '0')}` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** The tray's one-line header: "2 running · Reading the GPT-Live post · 0:42". */
export function trayHeader(jobs: ArturoJob[], now = Date.now() / 1000): string {
  const running = jobs.filter((j) => j.status === 'running');
  if (running.length === 0) {
    const failed = jobs.filter((j) => j.status === 'failed' || j.status === 'lost');
    return failed.length ? `${failed.length} didn't finish · ${failed[failed.length - 1].title}` : '';
  }
  const latest = running[running.length - 1];
  return `${running.length} running · ${latest.title} · ${elapsed(latest, now)}`;
}

/** Ids of jobs that were running in `before` and have ended in `after`: their results are now in the thread. */
export function newlyEnded(before: ArturoJob[], after: ArturoJob[]): string[] {
  const was = new Set(before.filter((j) => j.status === 'running').map((j) => j.id));
  return after.filter((j) => was.has(j.id) && j.status !== 'running').map((j) => j.id);
}
