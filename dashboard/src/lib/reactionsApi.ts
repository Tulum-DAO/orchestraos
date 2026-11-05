/**
 * reactionsApi.ts — the dashboard's side of /api/reactions (api/src/routes/reactions.ts, build step 1):
 *
 *   GET  /api/reactions?surface&conv          -> { surface, conv, reactions: { <ref>: [{ emoji, reactor, at }] } }
 *   PUT  /api/reactions {surface, conv, ref, emoji}  the caller's one emoji; the same again removes it
 *                                             -> { ok, surface, conv, ref, reactor, emoji|null, at }
 *   GET  /api/reactions/stream?surface&conv   SSE `event: reaction`, data = one change
 *   GET  /api/me                              -> { username, ... }: who "mine" is
 *
 * One EventSource per conversation however many views read it (an open chat, its compact card), closed with the last.
 * A stream that drops and comes back says so (onReopen), so the reader re-reads what it missed.
 * fetch and EventSource are injectable for the tests.
 */
import type { ReactionChange, ReactionMap, Reactor, StoredReaction, Surface } from './reactionState';

const BASE = '/api/reactions';
const qs = (surface: Surface, conv: string) => `surface=${encodeURIComponent(surface)}&conv=${encodeURIComponent(conv)}`;

const isReactor = (v: unknown): v is Reactor =>
  !!v && typeof v === 'object' && ((v as Reactor).kind === 'user' || (v as Reactor).kind === 'agent') && typeof (v as Reactor).id === 'string';

function stored(v: unknown): StoredReaction | null {
  const r = v as StoredReaction;
  return r && typeof r.emoji === 'string' && r.emoji && isReactor(r.reactor) ? { emoji: r.emoji, reactor: r.reactor, at: String(r.at ?? '') } : null;
}

/** One conversation's reactions by ref; throws on a refusal (the caller keeps what it has). */
export async function fetchReactions(surface: Surface, conv: string, fetchFn: typeof fetch = fetch): Promise<ReactionMap> {
  const r = await fetchFn(`${BASE}?${qs(surface, conv)}`);
  if (!r.ok) throw new Error(`reactions ${r.status}`);
  const body = await r.json() as { reactions?: Record<string, unknown> };
  const out: Record<string, StoredReaction[]> = {};
  for (const [ref, list] of Object.entries(body.reactions ?? {})) {
    const ok = (Array.isArray(list) ? list : []).map(stored).filter((x): x is StoredReaction => !!x);
    if (ok.length) out[ref] = ok;
  }
  return out;
}

/** A change from the server (the PUT's answer or a stream frame), or null when it is not one. */
export function parseChange(v: unknown): ReactionChange | null {
  const c = v as ReactionChange;
  if (!c || typeof c !== 'object' || typeof c.ref !== 'string' || !c.ref || !isReactor(c.reactor)) return null;
  if (c.emoji !== null && (typeof c.emoji !== 'string' || !c.emoji)) return null;
  return { ...(c.surface ? { surface: c.surface } : {}), ...(c.conv ? { conv: c.conv } : {}), ref: c.ref, reactor: c.reactor, emoji: c.emoji, at: String(c.at ?? '') };
}

export type PutResult = { ok: true; change: ReactionChange } | { ok: false; error: string };

/** Set (or, the same emoji again, remove) the viewer's reaction. Never throws: the error to show instead. */
export async function putReaction(surface: Surface, conv: string, ref: string, emoji: string, fetchFn: typeof fetch = fetch): Promise<PutResult> {
  try {
    const r = await fetchFn(BASE, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ surface, conv, ref, emoji }),
    });
    const d = await r.json().catch(() => ({}));
    const change = r.ok && d?.ok ? parseChange(d) : null;
    if (change) return { ok: true, change };
    return { ok: false, error: d?.error || `Couldn't save the reaction (HTTP ${r.status})` };
  } catch {
    return { ok: false, error: "Couldn't reach the server" };
  }
}

let me: Promise<Reactor | null> | null = null;
/** The viewer as a reactor (GET /api/me), read once per page; a failure is null and is tried again next time. */
export function loadMe(fetchFn: typeof fetch = fetch): Promise<Reactor | null> {
  if (!me) {
    me = fetchFn('/api/me').then((r) => (r.ok ? r.json() : null)).then((d) => (typeof d?.username === 'string' && d.username
      ? { kind: 'user' as const, id: d.username } : null)).catch(() => null);
    void me.then((m) => { if (!m) me = null; });
  }
  return me;
}
/** Tests only: forget the viewer read so far. */
export function resetMe(): void { me = null; }

/** onReopen: the stream dropped and came back. onClosed: the browser gave up on it (readyState CLOSED): it has been
 *  removed from the shared map, so the reader subscribes again (a new stream) and re-reads. */
export interface ReactionStreamHandlers { onChange: (c: ReactionChange) => void; onReopen?: () => void; onClosed?: () => void }
type ES = Pick<EventSource, 'addEventListener' | 'close'> & { readyState?: number; onopen: ((e: Event) => void) | null; onerror: ((e: Event) => void) | null };
type ESCtor = new (url: string) => ES;

const streams = new Map<string, { es: ES; readers: Set<ReactionStreamHandlers>; dropped: boolean }>();

/** Follow a conversation's reactions. Returns the unsubscribe. No EventSource (an old browser, a test): nothing. */
export function subscribeReactions(surface: Surface, conv: string, h: ReactionStreamHandlers,
  ESClass: ESCtor | undefined = typeof EventSource === 'undefined' ? undefined : (EventSource as unknown as ESCtor)): () => void {
  if (!ESClass) return () => {};
  const url = `${BASE}/stream?${qs(surface, conv)}`;
  let s = streams.get(url);
  if (!s) {
    const es = new ESClass(url);
    const entry = { es, readers: new Set<ReactionStreamHandlers>(), dropped: false };
    es.addEventListener('reaction', (e: Event) => {
      let c: ReactionChange | null = null;
      try { c = parseChange(JSON.parse((e as MessageEvent).data)); } catch { /* a malformed frame: the next re-read heals */ }
      if (c) entry.readers.forEach((r) => r.onChange(c!));
    });
    // The browser reconnects by itself (the route sends retry: 3000); what changed meanwhile is read again.
    es.onerror = () => {
      entry.dropped = true;
      if (es.readyState === 2) {                                   // EventSource.CLOSED: it will not reconnect by itself
        if (streams.get(url) === entry) streams.delete(url);
        es.close();
        const readers = [...entry.readers];
        entry.readers.clear();
        readers.forEach((r) => r.onClosed?.());
      }
    };
    es.onopen = () => { if (entry.dropped) { entry.dropped = false; entry.readers.forEach((r) => r.onReopen?.()); } };
    streams.set(url, entry);
    s = entry;
  }
  s.readers.add(h);
  const mine = s;
  return () => {
    mine.readers.delete(h);
    if (!mine.readers.size) { mine.es.close(); if (streams.get(url) === mine) streams.delete(url); }
  };
}
