/**
 * useConverse — thin React selector over the shared zustand converse store
 * (B3, Decision A). The conversation state + SSE stream loop live in
 * `useConverseStore` OUTSIDE the React tree, so both the bubble and the full
 * page share ONE (user, thread) conversation and an in-flight stream survives
 * route unmount.
 *
 * `channel` is a render-only attribute: it's passed on each send request but
 * never scopes state (both surfaces share the same conversation).
 */
import { useCallback } from 'react';
import { useConverseStore } from '../stores/useConverseStore';
import type { TurnItem } from '../lib/assistant/timeline';
import type { ActivityState } from '../lib/assistant/activity';

export type ConverseStatus = 'idle' | 'streaming' | 'error';

export interface UseConverseArgs {
  /** Render surface ("bubble" | "page" | ...). Passed per-send; never scopes state. */
  channel: string;
}

export interface UseConverse {
  items: TurnItem[];
  status: ConverseStatus;
  error: string | null;
  streaming: boolean;
  loadingThread: boolean;
  thread: string;
  activity: ActivityState;
  send: (text: string) => void;
  cancel: () => void;
  loadThread: (thread: string) => void;
  newThread: () => void;
}

export function useConverse({ channel }: UseConverseArgs): UseConverse {
  const items = useConverseStore((s) => s.items);
  const status = useConverseStore((s) => s.status);
  const error = useConverseStore((s) => s.error);
  const activity = useConverseStore((s) => s.activity);
  const loadingThread = useConverseStore((s) => s.loadingThread);
  const thread = useConverseStore((s) => s.thread);
  const storeSend = useConverseStore((s) => s.send);
  const cancel = useConverseStore((s) => s.cancel);
  const storeLoad = useConverseStore((s) => s.loadThread);
  const newThread = useConverseStore((s) => s.newThread);

  const send = useCallback((text: string) => { void storeSend(text, channel); }, [storeSend, channel]);
  const loadThread = useCallback((t: string) => { void storeLoad(t); }, [storeLoad]);

  return {
    items,
    status,
    error,
    streaming: status === 'streaming',
    loadingThread,
    thread,
    activity,
    send,
    cancel,
    loadThread,
    newThread,
  };
}
