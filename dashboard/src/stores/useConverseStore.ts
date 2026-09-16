/**
 * useConverseStore — shared /v2 converse conversation (B3, Decision A = zustand).
 *
 * The conversation lives OUTSIDE the React tree in a zustand store, keyed
 * (user, thread), so an in-flight SSE stream survives route/unmount and keeps
 * painting in BOTH the bubble and the full page. `useConverse` is a thin
 * selector over this store.
 *
 * DOUBLE-SUBMIT-PROOF (hard requirement): `send` is guarded at the DISPATCH
 * layer, not just by a disabled button. The `streaming` flag is set
 * SYNCHRONOUSLY at the very top of `send`, before the first `await`, so a
 * second intent from EITHER surface (rapid click, Enter, quick-action, bubble
 * submit during a page turn) sees `streaming === true` and no-ops — exactly one
 * converse turn / one network stream per intent.
 *
 * Token painting: deltas fold into the timeline via per-delta `set` with NO
 * debounce — the store paints each token as it arrives.
 */
import { create } from 'zustand';
import { converseStream } from '../lib/assistant/sse-client';
import type { ConverseRequest } from '../lib/assistant/contract';
import {
  applyStreamEvent,
  beginTurn,
  emptyTimeline,
  type TimelineState,
  type TurnItem,
} from '../lib/assistant/timeline';
import {
  deriveActivity,
  initialActivity,
  type ActivityState,
} from '../lib/assistant/activity';
import { getThread, hydrateTimeline } from '../lib/assistant/threads';

export type ConverseStatus = 'idle' | 'streaming' | 'error';

interface ConverseStore {
  user: string;
  thread: string;
  timeline: TimelineState;
  status: ConverseStatus;
  error: string | null;
  activity: ActivityState;
  /** Internal — the in-flight stream's aborter (null when idle). */
  _controller: AbortController | null;

  /** Derived convenience for consumers. */
  items: TurnItem[];

  /** True while a thread replay is being fetched/hydrated. */
  loadingThread: boolean;

  /**
   * Dispatch one converse turn. Double-submit-proof: no-ops if a stream is
   * already in flight. `channel` is render-only and does not scope state.
   */
  send: (text: string, channel: string) => Promise<void>;
  /** Abort the in-flight stream (if any). */
  cancel: () => void;
  /** Reset the whole conversation (used by tests / new-thread). */
  reset: () => void;

  /**
   * Resume a saved thread: aborts any in-flight stream, fetches the replay, and
   * hydrates the timeline (incl. persisted gate cards). No-op if already on it
   * and not empty. Sets `thread` — the shared key both surfaces render.
   */
  loadThread: (thread: string) => Promise<void>;
  /** Start a fresh per-session thread (the operator ruling: per-session w/ resume). */
  newThread: () => void;
}

/** Per-session thread id (the operator ruling #1). Distinct, resumable, sortable. */
function freshThreadId(): string {
  return `sess-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

export const useConverseStore = create<ConverseStore>((set, get) => ({
  user: 'operator',
  thread: 'default',
  timeline: emptyTimeline(),
  status: 'idle',
  error: null,
  activity: initialActivity(),
  _controller: null,
  items: [],
  loadingThread: false,

  send: async (text, channel) => {
    const trimmed = text.trim();
    // DISPATCH-LAYER GUARD: synchronous, before any await. One intent = one turn.
    if (!trimmed || get().status === 'streaming') return;

    const { user, thread } = get();
    const started = beginTurn(get().timeline, trimmed);
    const controller = new AbortController();
    // Commit 'streaming' + the user/assistant items SYNCHRONOUSLY so a second
    // concurrent send() sees streaming and bails.
    set({
      timeline: started,
      items: started.items,
      status: 'streaming',
      error: null,
      activity: deriveActivity(get().activity, { type: '_begin' }),
      _controller: controller,
    });

    const request: ConverseRequest = { user, thread, channel, text: trimmed };

    try {
      for await (const evt of converseStream(request, { signal: controller.signal })) {
        if (evt.type === 'error') {
          set({ error: evt.message, status: 'error', activity: deriveActivity(get().activity, evt), _controller: null });
          return;
        }
        if (evt.type === 'done') {
          set({ activity: deriveActivity(get().activity, evt) });
          break;
        }
        // delta + tool-call + activity events: fold into timeline + activity,
        // per-event (no debounce) so tokens paint as they arrive.
        const nextTimeline = applyStreamEvent(get().timeline, evt);
        set({
          timeline: nextTimeline,
          items: nextTimeline.items,
          activity: deriveActivity(get().activity, evt),
        });
      }
      set({ status: 'idle', _controller: null });
    } catch (e) {
      if (controller.signal.aborted) {
        set({ status: 'idle', activity: initialActivity(), _controller: null });
        return;
      }
      set({
        error: e instanceof Error ? e.message : 'stream failed',
        status: 'error',
        activity: { phase: 'error' },
        _controller: null,
      });
    }
  },

  cancel: () => {
    get()._controller?.abort();
    set({ status: 'idle', activity: initialActivity(), _controller: null });
  },

  reset: () => {
    get()._controller?.abort();
    set({
      timeline: emptyTimeline(),
      items: [],
      status: 'idle',
      error: null,
      activity: initialActivity(),
      _controller: null,
    });
  },

  loadThread: async (thread) => {
    if (get().thread === thread && get().items.length > 0) return;
    // Abort any in-flight stream before swapping conversations.
    get()._controller?.abort();
    set({
      thread,
      loadingThread: true,
      status: 'idle',
      error: null,
      activity: initialActivity(),
      _controller: null,
      timeline: emptyTimeline(),
      items: [],
    });
    try {
      const detail = await getThread(thread);
      // Guard against a race: only apply if we're still on this thread.
      if (get().thread !== thread) return;
      const hydrated = hydrateTimeline(detail);
      set({ timeline: hydrated, items: hydrated.items, loadingThread: false });
    } catch (e) {
      if (get().thread !== thread) return;
      set({ loadingThread: false, status: 'error', error: e instanceof Error ? e.message : 'load failed' });
    }
  },

  newThread: () => {
    get()._controller?.abort();
    set({
      thread: freshThreadId(),
      timeline: emptyTimeline(),
      items: [],
      status: 'idle',
      error: null,
      activity: initialActivity(),
      loadingThread: false,
      _controller: null,
    });
  },
}));
