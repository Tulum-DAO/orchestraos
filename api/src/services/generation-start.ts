/**
 * When an agent's CURRENT generation began: the line between cards its chat draws and cards that
 * belong to an earlier generation.
 *
 * Approval rows do not record which session filed them (their session columns are not filled), so
 * the boundary comes from the seat itself, the later of:
 *   - the identity store's promoted_at for the lineage's canonical generation (a rotation), and
 *   - the first timestamp in the current session's transcript (any new session on the seat, a
 *     rotation or a restart; a resumed session keeps its file, so its start does not move).
 * Either may be missing; with neither the answer is null and nothing is treated as earlier.
 */
import { openSync, readSync, closeSync } from 'fs';
import { getGenerations } from './identity-store-reader.js';
import { resolveTranscriptPath } from '../routes/chat-transcript.js';

const firstTsByPath = new Map<string, string | null>();

/** The first `timestamp` in a JSONL transcript (its opening lines can be metadata without one).
 *  Reads at most the first 256 KB. Cached per path: a file's first line never changes. */
export function firstTimestamp(path: string, read: (p: string) => string = headOf): string | null {
  if (firstTsByPath.has(path)) return firstTsByPath.get(path) ?? null;
  let ts: string | null = null;
  try {
    for (const line of read(path).split('\n')) {
      if (!line.trim()) continue;
      try {
        const o = JSON.parse(line);
        const t = typeof o?.timestamp === 'string' ? o.timestamp : null;
        if (t && !Number.isNaN(Date.parse(t))) { ts = t; break; }
      } catch { /* a partial last line, or not JSON */ }
    }
  } catch { return null; }                  // unreadable now: not cached, try again next time
  if (ts) firstTsByPath.set(path, ts);
  return ts;
}

function headOf(path: string): string {
  const fd = openSync(path, 'r');
  try {
    const buf = Buffer.alloc(256 * 1024);
    const n = readSync(fd, buf, 0, buf.length, 0);
    return buf.subarray(0, n).toString('utf-8');
  } finally {
    closeSync(fd);
  }
}

export interface GenerationStartDeps {
  promotedAt(agentId: string): string | null;
  transcriptPath(agentId: string): string | null;
  firstTs(path: string): string | null;
}

const realDeps: GenerationStartDeps = {
  promotedAt: (id) => getGenerations(id)?.find((g) => g.current)?.promoted_at ?? null,
  transcriptPath: (id) => resolveTranscriptPath(id).path,
  firstTs: (p) => firstTimestamp(p),
};

/** ISO time the agent's current generation began, or null when nothing says. */
export function currentGenerationStart(agentId: string, deps: GenerationStartDeps = realDeps): string | null {
  const times: number[] = [];
  const promoted = deps.promotedAt(agentId);
  if (promoted && !Number.isNaN(Date.parse(promoted))) times.push(Date.parse(promoted));
  const path = deps.transcriptPath(agentId);
  const first = path ? deps.firstTs(path) : null;
  if (first && !Number.isNaN(Date.parse(first))) times.push(Date.parse(first));
  return times.length ? new Date(Math.max(...times)).toISOString() : null;
}

/** Was this card filed before the current generation began? (Unknown start: no.) */
export function beforeGeneration(createdAt: string | null | undefined, start: string | null): boolean {
  if (!start || !createdAt) return false;
  const c = Date.parse(createdAt);
  return !Number.isNaN(c) && c < Date.parse(start);
}
