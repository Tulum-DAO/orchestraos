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

const HOME = process.env.HOME || homedir();
const ORCHESTRA_DIR = process.env.ORCHESTRA_DIR || join(HOME, '.orchestra');
const SESSIONS_PATH = join(ORCHESTRA_DIR, 'state', 'agent-sessions.json');

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
    sessionsCache = JSON.parse(readFileSync(SESSIONS_PATH, 'utf8')) || {};
  } catch {
    sessionsCache = {};          // absent/unreadable → no corroboration, detector stands
  }
  sessionsReadAt = now;
  return sessionsCache;
}

/**
 * Milliseconds since this agent's transcript was last appended to, or null when there is
 * no usable transcript path (never indexed, or the file is gone).
 */
export function transcriptAgeMs(agentId: string): number | null {
  const entry = sessions()[agentId];
  const cp = entry && entry.conversation_path;
  if (!cp || typeof cp !== 'string') return null;
  try {
    return Date.now() - statSync(cp).mtimeMs;
  } catch {
    return null;                 // stale path — say nothing rather than guess
  }
}

/** True only on positive evidence of an open turn. Unknown is never "working". */
export function isTranscriptActive(agentId: string): boolean {
  const age = transcriptAgeMs(agentId);
  return age !== null && age < WORK_WINDOW_MS;
}

export const WORK_WINDOW_MS_FOR_TEST = WORK_WINDOW_MS;
