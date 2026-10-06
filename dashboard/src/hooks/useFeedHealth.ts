/**
 * useFeedHealth — the ONE answer to "is the agent feed live?", for every surface that paints
 * agent state. Chip and page header read THIS, so the P1's chip-blue/page-yellow divergence
 * cannot recur: there is one source and one rule, not two renderers agreeing by coincidence.
 */
import { useEffect, useState } from 'react';
import { useAgents } from './useAgents';
import { feedHealthOf, type FeedVerdict } from '../lib/feedLiveness';

export function useFeedHealth(): FeedVerdict {
  const { data, dataUpdatedAt, isError } = useAgents();
  // A verdict is a function of the CLOCK as much as of the query: with the API down there are no
  // more query events, so without a tick the UI would sit on its last verdict and keep claiming
  // live. 5s is well under the 30s stale threshold.
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 5_000);
    return () => clearInterval(t);
  }, []);
  return feedHealthOf({ dataUpdatedAt, hasData: !!data, isError });
}
