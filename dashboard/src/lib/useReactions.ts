/**
 * useReactions — one conversation's reactions, live (reactions build step 2). A thin wrapper: the rules (one request in
 * flight per message, late echoes ignored, a GET never clobbering newer changes, rollback of the viewer's own entry only)
 * live in lib/reactionSync.ts, which is tested; this only starts and stops its store and hands React its snapshot.
 *
 * `me` is the viewer (GET /api/me): the badge they gave is tinted, and a tap on it takes it back. Until it is known a tap
 * still works; it just waits for the server's answer instead of showing at once.
 */
import { useEffect, useMemo, useSyncExternalStore } from 'react';
import { createReactionStore } from './reactionSync';
import { fetchReactions, loadMe, putReaction, subscribeReactions } from './reactionsApi';
import type { ReactionMap, Reactor, Surface } from './reactionState';

export interface ReactionsState {
  map: ReactionMap;
  me: Reactor | null;
  /** Why the last tap was not saved, for a few seconds. */
  error: string | null;
  /** The viewer taps `emoji` on the message `ref`: theirs if it was not, taken back if it was. */
  toggle: (ref: string, emoji: string) => void;
}

export function useReactions(surface: Surface, conv: string | null): ReactionsState {
  const store = useMemo(() => createReactionStore(conv, {
    fetch: () => fetchReactions(surface, conv ?? ''),
    put: (ref, emoji) => putReaction(surface, conv ?? '', ref, emoji),
    loadMe: () => loadMe(),
    subscribe: (h) => subscribeReactions(surface, conv ?? '', h),
  }), [surface, conv]);
  useEffect(() => store.start(), [store]);
  const snap = useSyncExternalStore(store.subscribe, store.getSnapshot);
  return { map: snap.map, me: snap.me, error: snap.error, toggle: store.toggle };
}
