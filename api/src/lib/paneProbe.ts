/**
 * paneProbe.ts: what the transcript route learns from a seat's tmux pane, without forking per poll.
 *
 * The operator asked for lower API CPU (2026-10-15): every transcript poll (phone/web, every few
 * seconds, per seat) forked `tmux list-panes` + `pgrep -P` (liveGeminiSid) and `tmux display-message`
 * (hookEventSid). Now:
 *  - ONE tmux call gives both answers (first pane's pid, active pane's id: the same panes the two old
 *    calls picked), cached per tmux session for TTL_MS. A miss is never cached, and a cached pane whose
 *    process is gone (/proc/<pid> missing: a respawned or killed pane) is asked of tmux again at once.
 *    A missing hook-event file is NOT staleness: codex/gemini panes never write one.
 *  - the pane's children come from /proc/<pid>/task/<tid>/children (every thread, as pgrep -P saw them),
 *    a file read, not a fork.
 *  - a resolved Gemini conversation id is cached per session for TTL_MS too (hits only).
 * `deps` is swappable so tests count tmux calls; production uses the real ones.
 */
import { execFileSync } from 'child_process';
import { readFileSync, readdirSync, readlinkSync } from 'fs';

export const TTL_MS = 10_000;

export interface PaneDeps {
  tmux(args: string[]): string;
  readText(path: string): string;
  readDir(path: string): string[];
  readLink(path: string): string;
  now(): number;
}

const REAL: PaneDeps = {
  tmux: (args) => execFileSync('tmux', args, { timeout: 2000 }).toString(),
  readText: (p) => readFileSync(p, 'utf-8'),
  readDir: (p) => readdirSync(p),
  readLink: (p) => readlinkSync(p),
  now: () => Date.now(),
};

let deps: PaneDeps = REAL;
const panes = new Map<string, { at: number; panePid: string | null; paneId: string | null }>();
const geminiSids = new Map<string, { at: number; sid: string }>();

/** Tests only: swap the deps (null = real) and start from empty caches. */
export function setPaneProbeDeps(d: PaneDeps | null): void {
  deps = d ?? REAL;
  panes.clear();
  geminiSids.clear();
}

/** First pane's pid + active pane's id (without the %) for a tmux session target, or nulls. */
export function paneInfo(tmuxSession: string): { panePid: string | null; paneId: string | null } {
  const hit = panes.get(tmuxSession);
  if (hit && deps.now() - hit.at < TTL_MS && hit.panePid && alive(hit.panePid)) {
    return { panePid: hit.panePid, paneId: hit.paneId };
  }
  panes.delete(tmuxSession);
  let out = '';
  try { out = deps.tmux(['list-panes', '-t', tmuxSession, '-F', '#{pane_pid} #{pane_id} #{pane_active}']); }
  catch { return { panePid: null, paneId: null }; }
  const rows = out.trim().split('\n').map((l) => l.trim().split(' ')).filter((r) => r.length >= 3 && r[0]);
  if (!rows.length) return { panePid: null, paneId: null };
  const panePid = rows[0][0];
  const active = rows.find((r) => r[2] === '1') ?? rows[0];
  const paneId = active[1].replace('%', '') || null;
  panes.set(tmuxSession, { at: deps.now(), panePid, paneId });
  return { panePid, paneId };
}

function alive(pid: string): boolean {
  try { deps.readDir(`/proc/${pid}/task`); return true; } catch { return false; }
}

/** The cached pane turned out stale: ask tmux again on the next poll. */
export function forgetPane(tmuxSession: string): void {
  panes.delete(tmuxSession);
  geminiSids.delete(tmuxSession);
}

/** Child pids of `pid` across all its threads (what `pgrep -P pid` lists), or null if pid is gone. */
export function childPids(pid: string): string[] | null {
  let tids: string[];
  try { tids = deps.readDir(`/proc/${pid}/task`); } catch { return null; }
  const out: string[] = [];
  for (const tid of tids) {
    try { out.push(...deps.readText(`/proc/${pid}/task/${tid}/children`).trim().split(/\s+/).filter(Boolean)); }
    catch { /* thread exited between readdir and read */ }
  }
  return out;
}

/** Live Antigravity / Gemini conversation id, from the fds of the pane's child processes. */
export function liveGeminiSid(tmuxSession: string): string | null {
  const hit = geminiSids.get(tmuxSession);
  if (hit && deps.now() - hit.at < TTL_MS) return hit.sid;
  geminiSids.delete(tmuxSession);
  const { panePid } = paneInfo(tmuxSession);
  if (!panePid) return null;
  const kids = childPids(panePid);
  if (kids === null) { forgetPane(tmuxSession); return null; }
  for (const pid of kids) {
    let fds: string[];
    try { fds = deps.readDir(`/proc/${pid}/fd`); } catch { continue; }
    for (const fd of fds) {
      try {
        const m = deps.readLink(`/proc/${pid}/fd/${fd}`).match(/antigravity-cli\/(?:presence|brain)\/([0-9a-f-]{36})/);
        if (m && m[1]) { geminiSids.set(tmuxSession, { at: deps.now(), sid: m[1] }); return m[1]; }
      } catch { /* fd closed */ }
    }
  }
  return null;
}

/** A process's argv from /proc/<pid>/cmdline (empty when it is gone). Public: the Grok probe's
 *  "is this the grok binary" check, read through the same deps so tests never touch real pids. */
export function procArgv(pid: string): string[] {
  try { return deps.readText(`/proc/${pid}/cmdline`).split('\0').filter(Boolean); }
  catch { return []; }
}
