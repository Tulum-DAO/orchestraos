/**
 * An agent's own background work, for the job tray under its avatar row: background shell commands, subagents (one
 * job each) and waits. Read from the agent's Claude Code transcript (the same file whether the seat runs headless or
 * in tmux), so every Claude seat has it; a headless seat's supervisor log adds when the CLI last exited, which ends
 * whatever was still running in it.
 *
 * The grammar, as Claude Code writes it:
 *  - a background command: a Bash tool_use with input.run_in_background, an immediate tool_result ("running in
 *    background"), then, when it ends, a <task-notification> naming its <tool-use-id> with a <status> (a queued_command
 *    attachment when it lands mid-turn, a user text line otherwise);
 *  - a subagent: a Task / Agent tool_use. Run in the foreground it ends with its tool_result; launched async the result
 *    says "Async agent launched" and it ends with its <task-notification>, as a background command does;
 *  - a wait: a Monitor, Sleep or Wait tool call, or a foreground Bash that only sleeps or polls with sleep; it ends with
 *    its result (or its notification, run in the background). A ScheduleWakeup answers at once and waits out its delay.
 * Lines a subagent writes (isSidechain) are its own work, never a job of the agent's.
 *
 * `result_uuid`: the transcript line a finished job's result landed on (the notification line, or the tool_result's
 * line), the row the chat already draws: the dashboard puts the job's result card there.
 *
 * A call the CLI refused never became a job: its first result is an error whose body is a <tool_use_error> (a blocked
 * `sleep 25`, a refused permission), or any error within REFUSED_WITHIN_MS of the call. It is left out. A job that
 * ran and then failed (a background command that exited non-zero, a subagent that errored after running) is a failed
 * job, with a short `reason`.
 */
import fs from 'fs';

export type JobKind = 'command' | 'subagent' | 'wait';
export type JobStatus = 'running' | 'done' | 'failed';
export interface Job {
  id: string; kind: JobKind; title: string; status: JobStatus;
  started_at: string | null; ended_at?: string; result_uuid?: string;
  /** Failed jobs only: why, in a few plain words. */
  reason?: string;
  /** The launching tool call's id (the same as `id`), and the transcript line that call is on: the uuid the thread's
   *  tool card for it is keyed by, so the dashboard can show the job's state on that card and scroll to it. */
  tool_use_id: string;
  launch_uuid?: string;
}

/** An error answered this soon after the call is the CLI turning it down, not a job that ran and failed. */
export const REFUSED_WITHIN_MS = 1000;
/** Finished jobs stay in the tray this long after they end; running ones always. */
export const FINISHED_KEEP_MS = 60 * 60 * 1000;

export const REASON_ERROR = 'exited with an error';
export const REASON_SUBAGENT_ERROR = 'ended with an error';
export const REASON_STOPPED = 'was stopped';
export const REASON_RESTARTED = 'the agent restarted';
export const REASON_UNFINISHED = "didn't finish";

const SUBAGENT_TOOLS = new Set(['Task', 'Agent']);
const WAIT_TOOLS = new Set(['Monitor', 'Sleep', 'Wait', 'ScheduleWakeup']);
// A foreground command that does nothing but wait: `sleep 30`, or a poll loop around a sleep.
const WAIT_COMMAND = /^\s*(?:sleep\s+[\d.]+[smh]?\s*(?:;|&&)?\s*$|(?:until|while)\b[\s\S]*\bsleep\b)/;
const NOTICE_ID = /<tool-use-id>\s*([^<\s]+)\s*<\/tool-use-id>/;
const NOTICE_STATUS = /<status>\s*([^<\s]+)\s*<\/status>/;

const isoOf = (v: unknown): string | null => (typeof v === 'string' && !Number.isNaN(Date.parse(v)) ? new Date(Date.parse(v)).toISOString() : null);
const short = (s: unknown, n = 80) => {
  const t = String(s ?? '').replace(/\s+/g, ' ').trim();
  return t.length > n ? `${t.slice(0, n - 1)}…` : t;
};

function resultText(c: any): string {
  if (typeof c === 'string') return c;
  if (Array.isArray(c)) return c.map((x: any) => (x && typeof x.text === 'string' ? x.text : '')).join('\n');
  return '';
}

function kindOf(name: string, input: any): JobKind | null {
  if (SUBAGENT_TOOLS.has(name)) return 'subagent';
  if (WAIT_TOOLS.has(name)) return 'wait';
  if (name === 'Bash') {
    if (input?.run_in_background === true) return 'command';
    if (typeof input?.command === 'string' && WAIT_COMMAND.test(input.command)) return 'wait';
  }
  return null;
}

function titleOf(kind: JobKind, name: string, input: any): string {
  if (kind === 'subagent') return short(input?.description || input?.subagent_type || input?.prompt || 'Subagent');
  if (name === 'Bash') return short(input?.description || input?.command || 'Command');
  return short(input?.description || input?.reason || input?.command || name);
}

function wakeDelayS(input: any): number | null {
  for (const k of ['delaySeconds', 'delay_seconds', 'seconds']) if (typeof input?.[k] === 'number') return input[k];
  if (typeof input?.minutes === 'number') return input.minutes * 60;
  return null;
}

/** A notification's own status: completed is done; failed, killed, an error or anything else is failed, with why. */
function noticeOutcome(text: string): { status: JobStatus; reason?: string } {
  const st = NOTICE_STATUS.exec(text)?.[1] || '';
  if (/^(completed|success|done)$/i.test(st)) return { status: 'done' };
  if (/^(killed|stopped|cancell?ed|interrupted|aborted)$/i.test(st)) return { status: 'failed', reason: REASON_STOPPED };
  if (/^(failed|error|errored)$/i.test(st)) return { status: 'failed', reason: REASON_ERROR };
  return { status: 'failed', reason: REASON_UNFINISHED };
}

/** Every job in one Claude Code log, in the order they started. `now` decides a ScheduleWakeup's end. */
export function parseJobs(lines: readonly string[], now: number = Date.now()): Job[] {
  const jobs = new Map<string, Job & { awaitNotice?: boolean; launched?: boolean; wakeUntil?: number }>();
  const finish = (id: string, status: JobStatus, ts: string | null, uuid: unknown, reason?: string) => {
    const j = jobs.get(id);
    if (!j || j.status !== 'running') return;
    j.status = status;
    if (ts) j.ended_at = ts;
    if (typeof uuid === 'string' && uuid) j.result_uuid = uuid;
    if (status === 'failed' && reason) j.reason = reason;
  };
  const notice = (text: string, ts: string | null, uuid: unknown) => {
    if (!/<task-notification>/.test(text)) return;
    const id = NOTICE_ID.exec(text)?.[1];
    if (!id) return;
    const { status, reason } = noticeOutcome(text);
    finish(id, status, ts, uuid, reason);
  };
  for (const raw of lines) {
    if (!raw.trim()) continue;
    let o: any;
    try { o = JSON.parse(raw); } catch { continue; }
    if (o?.isSidechain) continue;
    const ts = isoOf(o?.timestamp);
    if (o?.type === 'attachment' && typeof o.attachment?.prompt === 'string') { notice(o.attachment.prompt, ts, o.uuid); continue; }
    if (o?.type !== 'assistant' && o?.type !== 'user') continue;
    const content = o.message?.content;
    if (typeof content === 'string') { if (o.type === 'user') notice(content, ts, o.uuid); continue; }
    if (!Array.isArray(content)) continue;
    for (const b of content) {
      if (!b || typeof b !== 'object') continue;
      if (o.type === 'assistant' && b.type === 'tool_use' && typeof b.id === 'string') {
        const name = String(b.name || '');
        const kind = kindOf(name, b.input);
        if (!kind || jobs.has(b.id)) continue;
        jobs.set(b.id, { id: b.id, kind, title: titleOf(kind, name, b.input), status: 'running', started_at: ts,
          tool_use_id: b.id, ...(typeof o.uuid === 'string' && o.uuid ? { launch_uuid: o.uuid } : {}),
          ...(name === 'ScheduleWakeup' ? { wakeUntil: (ts ? Date.parse(ts) : now) + (wakeDelayS(b.input) ?? 0) * 1000 } : {}),
          ...(b.input?.run_in_background === true ? { awaitNotice: true } : {}) });
      } else if (o.type === 'user' && b.type === 'tool_result' && typeof b.tool_use_id === 'string') {
        const j = jobs.get(b.tool_use_id);
        if (!j || j.status !== 'running') continue;
        const text = resultText(b.content);
        const refusal = /^\s*<tool_use_error>/.test(text);
        const failed = b.is_error === true || refusal;
        if (failed) {
          // Refused, not run: the first answer to the call is the CLI turning it down. Never a job.
          const quick = !!(ts && j.started_at) && Date.parse(ts) - Date.parse(j.started_at!) <= REFUSED_WITHIN_MS;
          if (!j.launched && (refusal || quick)) { jobs.delete(j.id); continue; }
          const stopped = /\[Request interrupted|interrupted by user|was (?:stopped|cancell?ed|killed)/i.test(text);
          finish(j.id, 'failed', ts, o.uuid, stopped ? REASON_STOPPED : j.kind === 'subagent' ? REASON_SUBAGENT_ERROR : REASON_ERROR);
          continue;
        }
        // Launched, not finished: it ends with its notification (or, for a wakeup, its delay).
        if (j.awaitNotice || /^\s*Async agent launched/i.test(text)) { j.awaitNotice = true; j.launched = true; continue; }
        if (j.wakeUntil !== undefined) continue;
        finish(j.id, 'done', ts, o.uuid);
      } else if (o.type === 'user' && b.type === 'text' && typeof b.text === 'string') {
        notice(b.text, ts, o.uuid);
      }
    }
  }
  const out: Job[] = [];
  for (const j of jobs.values()) {
    const { awaitNotice: _a, launched: _l, wakeUntil, ...job } = j;
    if (job.status === 'running' && wakeUntil !== undefined && now >= wakeUntil) {
      job.status = 'done';
      job.ended_at = new Date(wakeUntil).toISOString();
    }
    out.push(job);
  }
  return out;
}

/** Whatever was still running when the CLI last exited ended with it: a background command and an async subagent
 *  are the CLI's own children. `exitAt` from the headless supervisor's log (lastCliExit). */
export function endWithCli(jobs: Job[], exitAt: string | null): Job[] {
  if (!exitAt) return jobs;
  const t = Date.parse(exitAt);
  return jobs.map((j) => (j.status === 'running' && j.started_at && Date.parse(j.started_at) < t
    ? { ...j, status: 'failed' as const, ended_at: new Date(t).toISOString(), reason: REASON_RESTARTED } : j));
}

/** When the headless seat's CLI last exited, from the tail of its supervisor log (state/headless/<seat>/events.jsonl).
 *  Null when there is no log or no exit in its last `tailBytes`. */
export function lastCliExit(eventsFile: string, tailBytes = 512 * 1024): string | null {
  let fd: number | null = null;
  try {
    const size = fs.statSync(eventsFile).size;
    const start = Math.max(0, size - tailBytes);
    fd = fs.openSync(eventsFile, 'r');
    const buf = Buffer.alloc(size - start);
    fs.readSync(fd, buf, 0, buf.length, start);
    const lines = buf.toString('utf-8').split('\n');
    for (let i = lines.length - 1; i >= 0; i--) {
      if (!lines[i].includes('"exit"')) continue;
      try {
        const r = JSON.parse(lines[i]);
        if (r?.kind === 'sup' && r.what === 'exit') return isoOf(r.wall);
      } catch { /* a cut first line */ }
    }
    return null;
  } catch { return null; } finally { if (fd !== null) try { fs.closeSync(fd); } catch { /* closed */ } }
}

/** The tray's list: every running job, then the newest finished ones (`keepDone`) that ended within FINISHED_KEEP_MS
 *  of `now`, newest first. A finished job with no end time is judged by when it started. */
export function trayJobs(jobs: Job[], keepDone = 20, now: number = Date.now()): Job[] {
  const newest = (a: Job, b: Job) => Date.parse(b.started_at || '') - Date.parse(a.started_at || '');
  const running = jobs.filter((j) => j.status === 'running').sort(newest);
  const recent = (j: Job) => {
    const t = Date.parse(j.ended_at || j.started_at || '');
    return !Number.isNaN(t) && now - t <= FINISHED_KEEP_MS;
  };
  const done = jobs.filter((j) => j.status !== 'running' && recent(j)).sort(newest).slice(0, keepDone);
  return [...running, ...done];
}

// Parsed per file while it is unchanged: the tray polls.
const cache = new Map<string, { sig: string; jobs: Job[] }>();

/** The jobs in one transcript file, re-read only when it changes. A ScheduleWakeup's end depends on the clock, so
 *  running wakeups are settled on each read. */
export function jobsInFile(path: string, now: number = Date.now()): Job[] {
  const st = fs.statSync(path);
  const sig = `${st.ino}:${st.mtimeMs}:${st.size}`;
  const hit = cache.get(path);
  if (hit && hit.sig === sig && !hit.jobs.some((j) => j.status === 'running' && j.kind === 'wait')) return hit.jobs;
  const jobs = parseJobs(fs.readFileSync(path, 'utf-8').split('\n'), now);
  cache.set(path, { sig, jobs });
  if (cache.size > 64) cache.delete(cache.keys().next().value as string);
  return jobs;
}
