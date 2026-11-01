/**
 * compactNow.ts — "Compact now" in an agent's Setup: what the row says and whether it can be tapped.
 *
 * A tap sends exactly what typing /compact in the chat sends (POST /api/agents/:id/send, text "/compact"). The
 * server answers a seat that can't compact yet with a refusal in words, shown as it is. It does not say ahead of time
 * for older servers; a server that does (`compact_now` on the seat's settings, its gateway's own gate) gets a row
 * that is not offered at all where it could only fail.
 *
 * How it went comes from what the chat already gets: the compaction notice (lib/compaction.ts) that the request
 * starts, "Compacting the chat…" then "The chat was compacted", or "Couldn't compact the chat right now.". Until it
 * starts (the agent finishes what it is doing first) the row says it is waiting. With "Show compaction messages" off
 * there are no notices, so the row says only that it was asked.
 */
import type { CompactionItem } from './compaction';

export const COMPACT_OFF = "Compacting isn't turned on for this agent yet.";

/** What happened to the last tap: nothing yet, sending, sent (at `at`, ms), or refused with the server's words. */
export type CompactAsk =
  | { phase: 'idle' }
  | { phase: 'sending'; at: number }
  | { phase: 'sent'; at: number }
  | { phase: 'refused'; text: string; final: boolean };

export interface CompactNowView {
  /** The button can be tapped. */
  enabled: boolean;
  /** Something is under way (a spinner beside the line). */
  busy: boolean;
  /** The line under the row ('' = none). */
  line: string;
  tone: 'note' | 'done' | 'error';
}

// A notice is the answer to this tap when it began after it (a little allowance for the two clocks).
const SKEW_MS = 5000;

export function compactNowView({ canCompact, ask, notice, showNotices }: {
  /** The server's `compact_now` (undefined: it does not say). */
  canCompact: boolean | undefined;
  ask: CompactAsk;
  /** The agent's newest compaction notice, as its chat shows it. */
  notice: CompactionItem | null;
  showNotices: boolean;
}): CompactNowView {
  if (canCompact === false) return { enabled: false, busy: false, line: COMPACT_OFF, tone: 'note' };
  if (ask.phase === 'refused') return { enabled: !ask.final, busy: false, line: ask.text, tone: 'error' };
  if (ask.phase === 'sending') return { enabled: false, busy: true, line: 'Asking…', tone: 'note' };
  if (ask.phase === 'sent') {
    const t = notice?.ts ? Date.parse(notice.ts) : NaN;
    const ours = notice && notice.trigger !== 'auto' && !Number.isNaN(t) && t >= ask.at - SKEW_MS ? notice : null;
    if (ours?.state === 'compacting') return { enabled: false, busy: true, line: 'Compacting the chat…', tone: 'note' };
    if (ours?.state === 'compacted') return { enabled: true, busy: false, line: 'The chat was compacted', tone: 'done' };
    if (ours?.state === 'failed') return { enabled: true, busy: false, line: "Couldn't compact the chat right now.", tone: 'error' };
    if (!showNotices) return { enabled: true, busy: false, line: 'Asked. The chat compacts once the agent pauses.', tone: 'note' };
    return { enabled: false, busy: true, line: 'Waiting for the agent to pause…', tone: 'note' };
  }
  return { enabled: true, busy: false, line: '', tone: 'note' };
}
