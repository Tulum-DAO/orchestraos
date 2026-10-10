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

/**
 * CONNECTING is not a degraded state, it is the ABSENCE of an answer yet.
 *
 * Collapsing it into `disconnected` is what made a healthy cold load announce an outage: the
 * first paint of every page had no data, so the one verdict said "Not connected to the fleet"
 * for the whole of the first fetch. "We have not asked yet" and "we asked and cannot reach it"
 * are different claims and only the second is an alarm.
 */
export type FeedHealth = 'connecting' | 'live' | 'updating' | 'stale' | 'disconnected';

/**
 * UPDATING (Shaw, 2026-10-10: every status went blank for ~2 min when he came back to the tab).
 * A hidden tab does not poll: the browser pauses the interval, so on return the last success is
 * minutes or hours old through no fault of the feed. Old data while a fetch is in flight (or about
 * to be, right after the tab came back), with no failure, is UPDATING: the last-known colours,
 * dimmed, never the grey of an outage. Only a fetch that FAILED or has been outstanding longer
 * than FETCH_OUTSTANDING_MS, or a poller that stayed silent while the tab was VISIBLE, is stale.
 */
/** A fetch outstanding this long is not "updating" any more; it is not answering. */
export const FETCH_OUTSTANDING_MS = 10_000;

/** Ten missed 3 s beats while visible is not a blip. */
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
  /** Whether a fetch is in flight right now. Distinguishes "not asked yet" from "cannot reach". */
  isFetching?: boolean;
  /** When the fetch now in flight started (epoch ms); undefined when none is or it is unknown. */
  fetchStartedAt?: number;
  /** When the tab last became visible (epoch ms). 0 / absent = visible all along. */
  visibleSince?: number;
  /** When the tab was hidden (epoch ms), while it IS hidden; absent = visible. */
  hiddenSince?: number;
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
export function feedHealthOf({ dataUpdatedAt = 0, hasData = false, isError = false, isFetching = false, fetchStartedAt, visibleSince = 0, hiddenSince, now = Date.now() }: FeedState): FeedVerdict {
  // HIDDEN: the clock stops at the moment the tab was hidden. The browser pauses the poll then, so
  // age that accrues while hidden says nothing about the feed, and a background tab that kept
  // re-verdicting would paint grey "disconnected" and show THAT frame first on return (probe:
  // hidden 130 s). Frozen, the hidden tab keeps its pre-hide verdict; an error still degrades it,
  // and on return visibleSince takes over (UPDATING until the refetch lands).
  if (hiddenSince) now = Math.min(now, hiddenSince);
  // Never heard anything: CONNECTING only while a fetch is actually in flight and has not yet
  // failed. The moment it errors, or stops being in flight without data, it is disconnected —
  // so this can never become the eternal spinner the old branch was written to prevent.
  if (!hasData || !dataUpdatedAt) {
    return { health: isFetching && !isError ? 'connecting' : 'disconnected' };
  }
  const ageMs = Math.max(0, now - dataUpdatedAt);
  const seen = { ageMs, lastSeenAt: dataUpdatedAt };
  const degraded = (): FeedVerdict => ({ health: ageMs >= DISCONNECTED_AFTER_MS ? 'disconnected' : 'stale', ...seen });
  // FAILING: the last fetch errored, or the one in flight has not answered in time.
  const outstandingMs = isFetching && fetchStartedAt ? Math.max(0, now - fetchStartedAt) : 0;
  if (isError || outstandingMs > FETCH_OUTSTANDING_MS) return degraded();
  if (ageMs < STALE_AFTER_MS) return { health: 'live', ...seen };
  // Old data, nothing failing. A fetch in flight is the refresh arriving.
  if (isFetching) return { health: 'updating', ...seen };
  // Nothing in flight. Just back from hidden: the refetch is about to fire, so still updating.
  // Visible for longer than a stale window with no success: the poller is silent while the
  // operator is LOOKING, and that is the P1, whatever the reason.
  return now - visibleSince < STALE_AFTER_MS ? { health: 'updating', ...seen } : degraded();
}

/** "last seen 16:30". Local time, because the operator reads a clock, not an offset. */
export function lastSeenLabel(lastSeenAt?: number): string {
  if (!lastSeenAt) return 'never connected';
  const d = new Date(lastSeenAt);
  return `last seen ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/**
 * Is the feed in a state the operator must be WARNED about?
 *
 * Every surface asks this rather than `health !== 'live'`, which silently included `connecting`
 * and so painted an alarm on first paint. The one rule lives here so a chip and a page header
 * cannot disagree — the same reason feedHealthOf itself is shared.
 */
export function isDegraded(verdict: FeedVerdict): boolean {
  return verdict.health === 'stale' || verdict.health === 'disconnected';
}

/**
 * The dot + label a surface must render when the feed is not live. GREY and NEVER pulsing: the
 * pulse is what made a dead feed read as an agent mid-turn. The label says when we last heard,
 * so "nothing is happening" and "we stopped being able to ask" are distinguishable at a glance.
 */
export function staleStyleFor(verdict: FeedVerdict): { dot: string; label: string } | undefined {
  // CONNECTING overrides nothing: there is no stale colour to correct when no colour has been
  // painted yet, and greying the fleet for the length of the first fetch is the same false
  // alarm as the banner.
  if (!isDegraded(verdict)) return undefined;
  return {
    dot: 'bg-neutral-600',
    label: verdict.health === 'disconnected'
      ? (verdict.lastSeenAt ? `disconnected · ${lastSeenLabel(verdict.lastSeenAt)}` : 'disconnected')
      : `stale · ${lastSeenLabel(verdict.lastSeenAt)}`,
  };
}

/**
 * The style for an agent's state, with the staleness override applied. EVERY surface that paints
 * agent state calls this — chip, canonical dot, card, chat pill. Reading STATE_STYLE directly is
 * how the P1 survived its own first fix: three surfaces had been taught the rule by their call
 * sites and a fourth, inline in AgentCard, had not. Found by the LIVE port proof, where the
 * /agents page still read "35 healthy" with the API dead for 45 seconds.
 */
export function styleForAgentState(
  feed: FeedVerdict | undefined,
  base: { label: string; dot: string; text: string },
): { label: string; dot: string; text: string } {
  const stale = feed && staleStyleFor(feed);
  if (stale) return { ...base, ...stale, text: 'text-neutral-400' };
  const updating = feed && updatingStyleFor(feed, base);
  return updating ?? base;
}

/**
 * UPDATING: the last-known colour, DIMMED and not pulsing (a pulse claims activity now), with
 * the label saying a refresh is on its way. Never grey: grey means we cannot reach the fleet.
 */
export function updatingStyleFor(
  verdict: FeedVerdict,
  base: { label: string; dot: string; text: string },
): { label: string; dot: string; text: string } | undefined {
  if (verdict.health !== 'updating') return undefined;
  const calm = (cls: string) => cls.split(/\s+/).filter((c) => c && c !== 'animate-pulse').join(' ');
  return { label: `${base.label} · status updating…`, dot: `${calm(base.dot)} opacity-40`, text: `${calm(base.text)} opacity-60` };
}
