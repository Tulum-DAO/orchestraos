/**
 * useFeedHealth — the ONE answer to "is the agent feed live?", for every surface that paints
 * agent state. Chip, card dot and page header read THIS, so the P1's chip-blue/page-yellow
 * divergence cannot recur: one source and one rule, not several renderers agreeing by accident.
 */
import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useAgents } from './useAgents';
import { feedHealthOf, type FeedVerdict } from '../lib/feedLiveness';

// ONE shared ticker for every subscriber. A verdict is a function of the CLOCK as much as of the
// query: with the API down there are no more query events, so without a tick the UI would sit on
// its last verdict and keep claiming live. A per-component interval would mean one timer per dot
// — 320 of them on the agents page — so they share this one.
const subs = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | undefined;
function subscribe(fn: () => void) {
  subs.add(fn);
  if (!timer) timer = setInterval(() => subs.forEach((f) => f()), 5_000);
  return () => {
    subs.delete(fn);
    if (subs.size === 0 && timer) { clearInterval(timer); timer = undefined; }
  };
}

// THE HIDDEN TAB (Shaw, 2026-10-10: every status went blank for ~2 min on returning to the tab).
// The browser pauses the 3 s poll while the tab is hidden, so on return the last success is old
// through no fault of the feed. Shared, like the ticker: WHEN the tab came back (feedHealthOf
// reads old data right after that as "updating", not stale), an immediate refetch so the wait
// is one fetch and not one poll interval, and when the fetch now in flight started (one that has
// not answered in FETCH_OUTSTANDING_MS is stale).
let visibleSince = 0;                                  // 0 = visible all along
let fetchStartedAt: number | undefined;
let refetchAgents: (() => void) | undefined;
let listening = false;
function onVisibilityChange() {
  if (document.visibilityState !== 'visible') return;
  visibleSince = Date.now();
  refetchAgents?.();
  subs.forEach((f) => f());                            // re-verdict at once: dimmed, never grey
}

export function useFeedHealth(): FeedVerdict {
  const { data, dataUpdatedAt, isError, isFetching } = useAgents();
  const qc = useQueryClient();
  const [, tick] = useState(0);
  useEffect(() => subscribe(() => tick((n) => n + 1)), []);
  useEffect(() => {
    // cancelRefetch:false joins a fetch already in flight instead of restarting it.
    refetchAgents = () => { void qc.refetchQueries({ queryKey: ['agents'], type: 'active' }, { cancelRefetch: false }); };
    if (!listening && typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibilityChange);
      listening = true;
    }
  }, [qc]);
  useEffect(() => {
    if (!isFetching) fetchStartedAt = undefined;
    else if (fetchStartedAt === undefined) fetchStartedAt = Date.now();
  }, [isFetching]);
  return feedHealthOf({ dataUpdatedAt, hasData: !!data, isError, isFetching, fetchStartedAt, visibleSince });
}
