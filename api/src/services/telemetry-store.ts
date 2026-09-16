/**
 * telemetry-store.ts — Build B READER of B1's realtime hot surfaces.
 *
 * Reads the two ephemeral, rebuildable-from-WAL files the LIVE B1 daemon writes
 * under ~/.orchestra/realtime/ (see scripts/lineage_daemon/realtime/snapshot.py):
 *   status.json            — per-seat DERIVED status (BODY-FREE metadata only)
 *   deltas/<session>.log    — court-GATED clean token frames (drop-oldest ring)
 *
 * This service NEVER runs the status deriver, NEVER scans /proc, NEVER writes.
 * Reading status metadata + already-court-gated clean frames in TS is NOT
 * "re-implementing the flag check" — the flag CHECK stays in B1 (telemetry-court
 * shells the Python seam). The delta relay is additionally gated by that check
 * at the route boundary (defense-in-depth; the write-gate is the primary).
 *
 * INERT: the daemon is unwired, so status.json / delta logs are ABSENT today.
 * Every reader degrades gracefully (null / empty) — the API serves an empty,
 * stale-flagged fleet rather than crashing.
 */
import { readFileSync } from 'fs';
import { join } from 'path';
import { homedir } from 'os';

const STATUS_SCHEMA = 'realtime-status/v1';
// Session ids become filenames — same safe charset as snapshot.py (_safe_session).
const UNSAFE_SESSION = /[^A-Za-z0-9._-]/g;

export interface SeatStatus {
  session: string;
  lineage_root?: string;
  runtime?: string;
  status?: string;
  ts?: number;
}
export interface StatusSnapshot {
  schema: string;
  ts: number;
  seats: Record<string, SeatStatus>;
}
export interface DeltaFrame { seq: number; ts: number; text: string }

/** Resolve the hot-surface dir: ORCHESTRA_REALTIME_DIR env > ~/.orchestra/realtime.
 *  Matches snapshot.realtime_dir() exactly. */
export function realtimeDir(): string {
  return process.env.ORCHESTRA_REALTIME_DIR || join(homedir(), '.orchestra', 'realtime');
}

function safeSession(session: string): string {
  const name = String(session).replace(UNSAFE_SESSION, '_');
  return name || '_';
}

/** The full body-free status snapshot, or null if absent/unreadable/wrong-schema
 *  (INERT daemon unwired => null => empty fleet). */
export function readFleetStatus(): StatusSnapshot | null {
  let obj: any;
  try {
    obj = JSON.parse(readFileSync(join(realtimeDir(), 'status.json'), 'utf-8'));
  } catch {
    return null;
  }
  if (!obj || typeof obj !== 'object' || obj.schema !== STATUS_SCHEMA) return null;
  if (!obj.seats || typeof obj.seats !== 'object') return null;
  return obj as StatusSnapshot;
}

export function readSeatStatus(session: string): SeatStatus | null {
  const snap = readFleetStatus();
  return (snap && snap.seats[session]) || null;
}

/**
 * Resolve a session -> its durable lineage_root from the snapshot.
 * Returns null when the session is absent OR its lineage_root is empty/missing.
 * BLOCKING-1 (DEC-1788461603): a null here MUST fail the delta stream CLOSED —
 * never let an unresolved session reach the flag store as a bare/None root.
 */
export function resolveLineageRoot(session: string): string | null {
  const seat = readSeatStatus(session);
  const root = seat && seat.lineage_root;
  return root && typeof root === 'string' ? root : null;
}

/** Clean delta frames with seq > afterSeq (oldest-first). Absent log => []. */
export function readDeltaFrames(session: string, afterSeq = 0): DeltaFrame[] {
  let raw: string;
  try {
    raw = readFileSync(join(realtimeDir(), 'deltas', safeSession(session) + '.log'), 'utf-8');
  } catch {
    return [];
  }
  const out: DeltaFrame[] = [];
  for (const ln of raw.split('\n')) {
    if (!ln) continue;
    try {
      const f = JSON.parse(ln);
      if (f && typeof f === 'object' && typeof f.seq === 'number' && f.seq > afterSeq) {
        out.push({ seq: f.seq, ts: f.ts, text: typeof f.text === 'string' ? f.text : '' });
      }
    } catch { /* skip malformed */ }
  }
  return out;
}
