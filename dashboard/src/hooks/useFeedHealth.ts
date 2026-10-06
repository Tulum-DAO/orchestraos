/**
 * useFeedHealth — the ONE answer to "is the agent feed live?", for every surface that paints
 * agent state. Chip, card dot and page header read THIS, so the P1's chip-blue/page-yellow
 * divergence cannot recur: one source and one rule, not several renderers agreeing by accident.
 */
import { useEffect, useState } from 'react';
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

export function useFeedHealth(): FeedVerdict {
  const { data, dataUpdatedAt, isError } = useAgents();
  const [, tick] = useState(0);
  useEffect(() => subscribe(() => tick((n) => n + 1)), []);
  return feedHealthOf({ dataUpdatedAt, hasData: !!data, isError });
}
