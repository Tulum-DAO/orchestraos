/**
 * Is a seat actually doing work right now?
 *
 * Why this exists (2026-09-29): the v2 detector reported `idle` for ALL 14 seats
 * simultaneously, including seats provably mid-turn. Sampled three times, three seconds
 * apart, against a seat that was demonstrably working: the detector said idle every time.
 * telemetryd was running and its output was fresh — it just classified everything idle.
 * So /agents could not honestly show "who is active right now", and any indicator built on
 * it would have rendered a permanently-idle badge that LOOKS authoritative.
 *
 * This is a corroborating signal, not a replacement: the detector still wins whenever it
 * reports work. It only fills in the case where the detector says idle but the seat's
 * transcript is being appended to, which cannot happen while a seat is genuinely idle.
 *
 * The signal itself is one stat() per seat: Claude appends to its transcript .jsonl on
 * every turn, so a recent mtime is direct evidence of an open turn. Deliberately NOT the
 * tmux pane-hash probe the /field dashboard also uses — that shells out to
 * `tmux capture-pane` per seat per request, which is far too expensive for a polled
 * endpoint, and the mtime half alone correctly identified every working seat when checked
 * against /field's own answer.
 *
 * Root-causing the detector is deliberately out of scope here — it is a multi-signal
 * system (hook state, turn resolution, CPU, IO deltas, byte-recency corroboration) and
 * deserves its own investigation rather than being fixed blind from a UI ticket.
 */
import { statSync, readFileSync } from 'fs';
import { join } from 'path';
import { homedir } from 'os';
import { resolveTranscriptPath } from '../routes/chat-transcript.js';
import { dataDir } from '../lib/config.js';

const HOME = process.env.HOME || homedir();
const CLAUDE_PROJECTS = join(HOME, '.claude', 'projects');
/** The resolver reads this same data dir, so the sid and its path come from one registry. */
function sessionsPath(): string {
  return join(dataDir(), 'state', 'agent-sessions.json');
}

/**
 * How recently the transcript must have been touched to count as working.
 *
 * Measured, not guessed: sampling an actively-working seat showed gaps of up to ~19s
 * between transcript writes (a long tool call writes nothing meanwhile). /field uses 8s,
 * which is fine for a fast-repainting 3D view where a flicker is invisible, but on an
 * org chart an 8s window would blink seats to "idle" mid-turn and read as broken. 45s
 * clears the observed gap with headroom while still going idle promptly once a seat
 * actually stops. Override if the cadence changes.
 */
const WORK_WINDOW_MS = Number(process.env.ORCHESTRA_WORK_WINDOW_MS || 45_000);

/** Re-read the session map at most this often; it changes only when a seat rotates. */
const SESSIONS_TTL_MS = 10_000;

let sessionsCache: Record<string, any> = {};
let sessionsReadAt = 0;

function sessions(): Record<string, any> {
  const now = Date.now();
  if (now - sessionsReadAt < SESSIONS_TTL_MS) return sessionsCache;
  try {
    sessionsCache = JSON.parse(readFileSync(sessionsPath(), 'utf8')) || {};
  } catch {
    sessionsCache = {};          // absent/unreadable → no corroboration, detector stands
  }
  sessionsReadAt = now;
  return sessionsCache;
}

interface SessionEntry { conversation_path?: unknown; session_id?: unknown; cwd?: unknown }

/** A session id goes into a file path: plain id characters only, never a separator. */
const SESSION_ID = /^[0-9A-Za-z][0-9A-Za-z_-]{0,127}$/;

/**
 * Resolved paths by `<agent>:<sid>`. A found path is kept until the seat's sid changes (a new key);
 * a miss is retried after MISS_TTL_MS. The full resolver costs up to ~0.3 s per seat (sqlite, /proc,
 * directory scans), so it must never run for every seat on every poll of the list.
 */
const MISS_TTL_MS = 60_000;
const MAX_RESOLVED = 2000;
/**
 * Full resolves allowed per RESOLVE_WINDOW_MS. Most live session entries carry no cwd, so after an API
 * restart every seat needs the resolver once: unbounded, the first list poll would block for seconds.
 * Seats over the budget read unknown (never working) and resolve on a later poll.
 */
const RESOLVE_BUDGET = Number(process.env.ORCHESTRA_TRANSCRIPT_RESOLVE_BUDGET || 8);
const RESOLVE_WINDOW_MS = 1_000;
let budgetWindowAt = 0;
let budgetUsed = 0;
let resolveCalls = 0;

function takeResolveBudget(): boolean {
  const now = Date.now();
  if (now - budgetWindowAt >= RESOLVE_WINDOW_MS) { budgetWindowAt = now; budgetUsed = 0; }
  if (budgetUsed >= RESOLVE_BUDGET) return false;
  budgetUsed += 1;
  return true;
}
const resolvedCache = new Map<string, { path: string | null; at: number }>();

function remember(key: string, path: string | null): void {
  if (resolvedCache.size >= MAX_RESOLVED) resolvedCache.delete(resolvedCache.keys().next().value as string);
  resolvedCache.set(key, { path, at: Date.now() });
}

function fileExists(p: string): boolean {
  try { return statSync(p).isFile(); } catch { return false; }
}

/**
 * The seat's transcript file. An explicit conversation_path wins; otherwise the chat route's own
 * resolver finds it from the seat's session (the projection leaves conversation_path empty for
 * almost every seat, 2026-11-25). The resolved file counts only when its session IS the seat's
 * recorded session_id: a newest-file guess in a shared project dir could be another seat's, and
 * unknown must never read as working.
 */
function transcriptPath(agentId: string, entry: SessionEntry): string | null {
  const cp = entry.conversation_path;
  if (cp && typeof cp === 'string') return cp;
  const sid = typeof entry.session_id === 'string' ? entry.session_id : '';
  if (!sid || !SESSION_ID.test(sid)) return null;
  const key = `${agentId}:${sid}`;
  const hit = resolvedCache.get(key);
  if (hit && (hit.path !== null || Date.now() - hit.at < MISS_TTL_MS)) return hit.path;
  // 1. The direct file: a Claude seat's transcript is <projects>/<slug(cwd)>/<sid>.jsonl. One stat.
  let path: string | null = null;
  if (typeof entry.cwd === 'string' && entry.cwd) {
    const direct = join(CLAUDE_PROJECTS, entry.cwd.replace(/[/.]/g, '-'), `${sid}.jsonl`);
    if (fileExists(direct)) path = direct;
  }
  // 2. Otherwise the chat route's full resolver (other runtimes, moved cwds), accepted only when the
  // session it found IS the seat's recorded one.
  if (path === null) {
    if (!takeResolveBudget()) return null;   // not cached: this seat resolves on a later poll
    resolveCalls += 1;
    try {
      const r = resolveTranscriptPath(agentId);
      path = r.path && r.sid === sid ? r.path : null;
    } catch {
      path = null;               // a resolver failure is unknown, never working
    }
  }
  remember(key, path);
  return path;
}

/**
 * Milliseconds since this agent's transcript was last appended to, or null when there is
 * no usable transcript path (never indexed, or the file is gone).
 */
export function transcriptAgeMs(agentId: string): number | null {
  const entry = sessions()[agentId] as SessionEntry | undefined;
  if (!entry) return null;
  const cp = transcriptPath(agentId, entry);
  if (!cp) return null;
  try {
    return Date.now() - statSync(cp).mtimeMs;
  } catch {
    // stale path: say nothing rather than guess, and forget it so the next poll resolves afresh
    if (typeof entry.session_id === 'string') resolvedCache.delete(`${agentId}:${entry.session_id}`);
    return null;
  }
}

/** True only on positive evidence of an open turn. Unknown is never "working". */
export function isTranscriptActive(agentId: string): boolean {
  const age = transcriptAgeMs(agentId);
  return age !== null && age < WORK_WINDOW_MS;
}

export const WORK_WINDOW_MS_FOR_TEST = WORK_WINDOW_MS;
export const RESOLVE_BUDGET_FOR_TEST = RESOLVE_BUDGET;
export function resolveCallsForTest(): number { return resolveCalls; }
