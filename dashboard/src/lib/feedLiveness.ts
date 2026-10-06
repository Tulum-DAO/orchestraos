/**
 * feedLiveness.ts — is what we are showing LIVE, or just the last thing we were told?
 *
 * P1 2026-10-06 (Shaw): the API was down for an hour. The dashboard kept rendering the last
 * cached /api/agents rows — a chip BLUE for "decision waiting" and a page header pulsing YELLOW
 * for "working" — while the agent was idle and every send failed. Nothing on screen said the
 * feed was gone. Cached state presented as live state is worse than no state: it is a confident
 * wrong answer, and it was acted on.
 *
 * ONE RULE, used by every surface that renders agent state, so a chip and a page header cannot
 * disagree again. Pure, so the thresholds are pinned by tests rather than by hope.
 */

export type FeedHealth = 'live' | 'stale' | 'disconnected';

/** The feed refetches every 10s. Three missed beats is not a blip. */
export const STALE_AFTER_MS = 30_000;
/** Twelve missed beats: the feed is not coming back on its own within a glance. */
export const DISCONNECTED_AFTER_MS = 120_000;

export interface FeedState {
  /** react-query dataUpdatedAt: when a fetch last SUCCEEDED. 0 = never. */
  dataUpdatedAt?: number;
  /** Whether any data has ever arrived. Cached rows still count — that is the hazard. */
  hasData?: boolean;
  /** Whether the most recent fetch failed. */
  isError?: boolean;
  now?: number;
}

export interface FeedVerdict {
  health: FeedHealth;
  /** Age of the last SUCCESSFUL update, or undefined when nothing ever arrived. */
  ageMs?: number;
  /** Epoch ms of the last successful update, for a "last seen" label. */
  lastSeenAt?: number;
}

/**
 * Never-fetched and long-failed both read DISCONNECTED; a recent failure over fresh data reads
 * STALE, because one dropped poll is not an outage and flapping the whole UI grey on it would
 * teach the operator to ignore the signal — the same reason `stalled` is not `needs you`.
 *
 * An error NEVER reads live, even when the data is seconds old: a failing fetch means the next
 * answer is unknown, and "live" is a claim about now, not about then.
 */
export function feedHealthOf({ dataUpdatedAt = 0, hasData = false, isError = false, now = Date.now() }: FeedState): FeedVerdict {
  if (!hasData || !dataUpdatedAt) return { health: 'disconnected' };
  const ageMs = Math.max(0, now - dataUpdatedAt);
  if (ageMs >= DISCONNECTED_AFTER_MS) return { health: 'disconnected', ageMs, lastSeenAt: dataUpdatedAt };
  if (isError || ageMs >= STALE_AFTER_MS) return { health: 'stale', ageMs, lastSeenAt: dataUpdatedAt };
  return { health: 'live', ageMs, lastSeenAt: dataUpdatedAt };
}

/** "last seen 16:30". Local time, because the operator reads a clock, not an offset. */
export function lastSeenLabel(lastSeenAt?: number): string {
  if (!lastSeenAt) return 'never connected';
  const d = new Date(lastSeenAt);
  return `last seen ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/**
 * The dot + label a surface must render when the feed is not live. GREY and NEVER pulsing: the
 * pulse is what made a dead feed read as an agent mid-turn. The label says when we last heard,
 * so "nothing is happening" and "we stopped being able to ask" are distinguishable at a glance.
 */
export function staleStyleFor(verdict: FeedVerdict): { dot: string; label: string } | undefined {
  if (verdict.health === 'live') return undefined;
  return {
    dot: 'bg-neutral-600',
    label: verdict.health === 'disconnected'
      ? (verdict.lastSeenAt ? `disconnected · ${lastSeenLabel(verdict.lastSeenAt)}` : 'disconnected')
      : `stale · ${lastSeenLabel(verdict.lastSeenAt)}`,
  };
}
