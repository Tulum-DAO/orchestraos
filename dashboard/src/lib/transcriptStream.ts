/**
 * Transcript live-stream client (F1) — SSE backfill + tail + resume, with the
 * existing interval poll as the fallback lane.
 *
 * Protocol (GET /api/agents/:id/transcript/stream, server transcript-stream.ts):
 *   event: snapshot  -> full backfill (F0 v2 items + render_items); replaces list
 *   event: delta     -> only the newly-appended items; appended to list
 *   id: <session_id>:<offset> — the browser replays it as Last-Event-ID on
 *   auto-reconnect, so a resume receives just the missed tail (no refetch).
 *   event: live_text -> a headless seat's reply still being written (LiveTextEvent);
 *                       no id, text '' clears. See liveTextOf.
 *
 * GUARDRAILS (locked, DEC-1787826639):
 *  - Additive: on stream failure we surrender to the caller's poll path
 *    (onFallback) — the working 3s poll never regresses.
 *  - Items are F0 contract shapes (ChatItem from the generated types). This
 *    module never re-derives summaries or flattens tool calls.
 *  - Live agent STATE (working/idle/…) is NOT in this lane.
 */
import type { ChatItem, LiveTextEvent } from './transcript.gen';
import type { LiveText } from './threadKit/view';

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

/** The live words to draw after the thread: the blocks whose finished line is not among `items` yet (the server
 *  drops a block once the transcript has it, a beat after the delta that brought it; this closes that gap so the
 *  words are never drawn twice). Null when nothing is live. */
export function liveTextOf(ev: LiveTextEvent | null, items: ChatItem[]): LiveText | null {
  if (!ev || !Array.isArray(ev.blocks) || !ev.blocks.length) return null;
  const landed = new Set<string>();
  for (const it of items) { const u = (it as { uuid?: unknown }).uuid; if (typeof u === 'string') landed.add(u); }
  const text = ev.blocks.filter((b) => !(b.uuid && landed.has(b.uuid))).map((b) => b.text).filter((t) => t.trim()).join('\n\n');
  return text.trim() ? { text } : null;
}

export interface TranscriptStreamHandlers {
  /** Called with the full (reduced) item list after every snapshot/delta. */
  onItems: (items: ChatItem[]) => void;
  /** A live_text frame (headless seats only); text '' = nothing live. */
  onLiveText?: (ev: LiveTextEvent) => void;
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
  es.addEventListener('live_text', (raw: MessageEvent) => {
    try { handlers.onLiveText?.(JSON.parse(raw.data) as LiveTextEvent); } catch { /* malformed frame: the next one heals */ }
  });
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
