// In-process fan-out of reaction changes: PUT/DELETE publish, /api/reactions/stream subscribers hear it (and, in
// build step 3, the agent wake). One process, one API; nothing here is durable (the store is).
import type { ReactionChange } from '../lib/reactions-store.js';

type Listener = (change: ReactionChange) => void;
const listeners = new Set<Listener>();

/** Tell every listener. A listener that throws (a closed client) never fails the write that published. */
export function publishReaction(change: ReactionChange): void {
  for (const cb of [...listeners]) {
    try { cb(change); } catch { /* a closed client */ }
  }
}

/** Listen; returns the unsubscribe. */
export function onReaction(cb: Listener): () => void {
  listeners.add(cb);
  return () => { listeners.delete(cb); };
}

export function reactionListenerCount(): number {
  return listeners.size;
}
