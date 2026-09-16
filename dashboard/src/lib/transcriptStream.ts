/**
 * Transcript live-stream client (F1) — SSE backfill + tail + resume, with the
 * existing interval poll as the fallback lane.
 *
 * Protocol (GET /api/agents/:id/transcript/stream, server transcript-stream.ts):
 *   event: snapshot  -> full backfill (F0 v2 items + render_items); replaces list
 *   event: delta     -> only the newly-appended items; appended to list
 *   id: <session_id>:<offset> — the browser replays it as Last-Event-ID on
 *   auto-reconnect, so a resume receives just the missed tail (no refetch).
 *
 * GUARDRAILS (locked, DEC-1787826639):
 *  - Additive: on stream failure we surrender to the caller's poll path
 *    (onFallback) — the working 3s poll never regresses.
 *  - Items are F0 contract shapes (ChatItem from the generated types). This
 *    module never re-derives summaries or flattens tool calls.
 *  - Live agent STATE (working/idle/…) is NOT in this lane.
 */
import type { ChatItem } from './transcript.gen';

export interface TranscriptStreamEvent {
  type: 'snapshot' | 'delta';
  id: string;
  agent_id: string;
  session_id: string | null;
  grammar_version: number;
  items: ChatItem[];
  render_items?: unknown[];
  reset?: boolean;
}

/** Bound on the client-held item list (snapshot window is 150; deltas accrue). */
export const MAX_CLIENT_ITEMS = 500;

/** Consecutive onerror count (without an intervening open) that triggers poll fallback. */
const FALLBACK_AFTER_ERRORS = 3;

export function shouldFallback(consecutiveErrors: number): boolean {
  return consecutiveErrors >= FALLBACK_AFTER_ERRORS;
}

/** Pure stream reducer: snapshot replaces, delta appends, list stays bounded. */
export function applyStreamEvent(items: ChatItem[], ev: TranscriptStreamEvent): ChatItem[] {
  const next = ev.type === 'snapshot' ? [...ev.items] : [...items, ...ev.items];
  return next.length > MAX_CLIENT_ITEMS ? next.slice(-MAX_CLIENT_ITEMS) : next;
}

export interface TranscriptStreamHandlers {
  /** Called with the full (reduced) item list after every snapshot/delta. */
  onItems: (items: ChatItem[]) => void;
  /** Called once when the stream is abandoned — caller starts the interval poll. */
  onFallback: () => void;
}

/**
 * Open the SSE stream for an agent. Returns a disposer. If EventSource is
 * unavailable or the stream keeps erroring, calls onFallback exactly once —
 * the caller then runs today's poll path.
 */
export function subscribeTranscriptStream(
  agentId: string,
  limit: number,
  handlers: TranscriptStreamHandlers,
): () => void {
  if (typeof EventSource === 'undefined') {
    handlers.onFallback();
    return () => {};
  }

  const url = `/api/agents/${encodeURIComponent(agentId)}/transcript/stream?limit=${limit}`;
  const es = new EventSource(url);
  let items: ChatItem[] = [];
  let consecutiveErrors = 0;
  let disposed = false;

  const handle = (raw: MessageEvent) => {
    try {
      const ev = JSON.parse(raw.data) as TranscriptStreamEvent;
      items = applyStreamEvent(items, ev);
      handlers.onItems(items);
    } catch { /* malformed frame — ignore; next snapshot heals */ }
  };

  es.addEventListener('snapshot', handle);
  es.addEventListener('delta', handle);
  es.onopen = () => { consecutiveErrors = 0; };
  es.onerror = () => {
    consecutiveErrors++;
    if (shouldFallback(consecutiveErrors) && !disposed) {
      disposed = true;
      es.close();
      handlers.onFallback();
    }
  };

  return () => {
    disposed = true;
    es.close();
  };
}
