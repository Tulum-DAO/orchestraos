/**
 * reactionSync.ts — the state machine behind useReactions (reactions build step 2), pure and tested without a DOM.
 *
 * What it settles, in one place:
 *   - ONE request in flight per message for the viewer. Taps made meanwhile only change what the viewer WANTS (the last
 *     tap wins); when the request settles, one more is sent from the confirmed state to reach the wanted one (the PUT
 *     toggles: the same emoji again removes it, so what to send depends on what the server last confirmed).
 *   - A change from the server (stream frame or PUT reply) older than the newest one applied for that (message, reactor)
 *     is ignored, so a late echo can never bring back what a later change removed.
 *   - A GET snapshot never drops what arrived while it was in flight: changes seen meanwhile are applied over it again.
 *   - The viewer's wishes are laid over the confirmed map when drawing (`viewMap`), touching only the viewer's own entry
 *     of a message. A refused request just forgets the wish: the confirmed map was never changed, so only the viewer's own
 *     entry reverts, and nobody else's.
 */
import {
  applyChange, nextEmoji, sameReactor, EMPTY_REACTIONS,
  type ReactionChange, type ReactionMap, type Reactor, type StoredReaction,
} from './reactionState';
import type { PutResult, ReactionStreamHandlers } from './reactionsApi';

export interface Intent { want: string | null; inflight: string | null }
export interface SyncState {
  base: ReactionMap;
  /** The newest `at` applied per (message, reactor), removals included. */
  stamps: Readonly<Record<string, string>>;
  intents: Readonly<Record<string, Intent>>;
  seq: number;
  /** Tokens of the GETs in flight. */
  reads: readonly number[];
  /** Changes seen while a GET was in flight (replayed over its snapshot). */
  buffer: readonly { seq: number; change: ReactionChange }[];
}

export const initialSync = (): SyncState => ({ base: EMPTY_REACTIONS, stamps: {}, intents: {}, seq: 0, reads: [], buffer: [] });

/** Where a not-yet-confirmed reaction sorts: after everything the server has stamped. */
export const PENDING_AT = '￿';

const stampKey = (ref: string, r: Reactor) => `${ref}\u0000${r.kind}:${r.id}`;

function applyStamped(s: SyncState, c: ReactionChange): SyncState {
  const k = stampKey(c.ref, c.reactor);
  const seen = s.stamps[k];
  if (seen && c.at && c.at < seen) return s;                       // older than what is applied: a late echo
  return { ...s, base: applyChange(s.base, c), stamps: c.at && (!seen || c.at > seen) ? { ...s.stamps, [k]: c.at } : s.stamps };
}

/** A change from the server (a stream frame or a PUT's answer). */
export function applyServer(s: SyncState, c: ReactionChange): SyncState {
  const next = applyStamped(s, c);
  return s.reads.length ? { ...next, seq: next.seq + 1, buffer: [...next.buffer, { seq: s.seq, change: c }] } : next;
}

/** A GET is starting: changes from now on are kept to be laid over its snapshot. */
export function beginRead(s: SyncState): { state: SyncState; token: number } {
  return { state: { ...s, reads: [...s.reads, s.seq], seq: s.seq + 1 }, token: s.seq };
}

function endRead(s: SyncState, token: number): SyncState {
  const i = s.reads.indexOf(token);
  const reads = i < 0 ? s.reads : [...s.reads.slice(0, i), ...s.reads.slice(i + 1)];
  const floor = reads.length ? Math.min(...reads) : Infinity;
  return { ...s, reads, buffer: s.buffer.filter((b) => b.seq >= floor) };
}

/** The GET failed: keep what is shown. */
export const abortRead = endRead;

/** The GET's snapshot arrived: it replaces the map, then every change seen since it started is applied over it. */
export function finishRead(s: SyncState, token: number, snapshot: ReactionMap): SyncState {
  const replay = s.buffer.filter((b) => b.seq >= token);
  const stamps: Record<string, string> = { ...s.stamps };
  for (const [ref, list] of Object.entries(snapshot)) {
    for (const r of list) { const k = stampKey(ref, r.reactor); if (r.at && (!stamps[k] || r.at > stamps[k])) stamps[k] = r.at; }
  }
  let next: SyncState = { ...s, base: snapshot, stamps };
  for (const b of replay) next = applyStamped(next, b.change);
  return endRead(next, token);
}

const mine = (list: readonly StoredReaction[] | undefined, me: Reactor): StoredReaction | undefined =>
  list?.find((r) => sameReactor(r.reactor, me));

/** What the viewer sees for one message: the confirmed reactions with the viewer's own wish laid over their entry. */
export function viewList(s: SyncState, ref: string, me: Reactor | null): readonly StoredReaction[] {
  const list = s.base[ref] ?? [];
  const it = s.intents[ref];
  if (!it || !me) return list;
  const had = mine(list, me);
  const rest = list.filter((r) => !sameReactor(r.reactor, me));
  if (it.want === null) return rest;
  return [...rest, { emoji: it.want, reactor: me, at: had && had.emoji === it.want ? had.at : PENDING_AT }];
}

export function viewMap(s: SyncState, me: Reactor | null): ReactionMap {
  const refs = Object.keys(s.intents);
  if (!refs.length || !me) return s.base;
  const out: Record<string, readonly StoredReaction[]> = { ...s.base };
  for (const ref of refs) { const l = viewList(s, ref, me); if (l.length) out[ref] = l; else delete out[ref]; }
  return out;
}

/** The viewer taps `emoji`: what they want the message to show becomes theirs, or gone when it already was. */
export function tap(s: SyncState, ref: string, emoji: string, me: Reactor): SyncState {
  const want = nextEmoji(viewList(s, ref, me), me, emoji);
  return { ...s, intents: { ...s.intents, [ref]: { want, inflight: s.intents[ref]?.inflight ?? null } } };
}

/** The request to send for one message now, if any: none while one is in flight, or when the confirmed state already is
 *  the wanted one. The emoji to PUT: the wanted one, or (to remove) the one the server has now. */
export function takeSend(s: SyncState, ref: string, me: Reactor): { state: SyncState; send: string | null } {
  const it = s.intents[ref];
  if (!it || it.inflight !== null) return { state: s, send: null };
  const cur = mine(s.base[ref], me)?.emoji ?? null;
  if (it.want === cur) {
    const intents = { ...s.intents }; delete intents[ref];
    return { state: { ...s, intents }, send: null };
  }
  const send = it.want ?? cur;
  if (send === null) return { state: s, send: null };
  return { state: { ...s, intents: { ...s.intents, [ref]: { ...it, inflight: send } } }, send };
}

/** A request settled. Answered: the server's change is applied and the next send, if the wish moved meanwhile, is
 *  returned. Refused: the wish is forgotten (only the viewer's own entry of that message reverts). */
export function settle(s: SyncState, ref: string, r: PutResult, me: Reactor): { state: SyncState; send: string | null; error: string | null } {
  const it = s.intents[ref];
  if (!r.ok) {
    const intents = { ...s.intents }; delete intents[ref];
    return { state: { ...s, intents }, send: null, error: r.error };
  }
  const cleared = it ? { ...s, intents: { ...s.intents, [ref]: { ...it, inflight: null } } } : s;
  return { ...takeSend(applyServer(cleared, r.change), ref, me), error: null };
}

// ---- the store the hook wraps ------------------------------------------------------------------------------

export interface ReactionsSnapshot { map: ReactionMap; me: Reactor | null; error: string | null }

export interface ReactionsApiLike {
  fetch: () => Promise<ReactionMap>;
  put: (ref: string, emoji: string) => Promise<PutResult>;
  loadMe: () => Promise<Reactor | null>;
  subscribe: (h: ReactionStreamHandlers) => () => void;
}

export interface StoreOptions { errorMs?: number; resubscribeMs?: number; resubscribeMaxMs?: number; stableMs?: number; setTimer?: (f: () => void, ms: number) => unknown; clearTimer?: (t: unknown) => void }

const EMPTY_SNAPSHOT: ReactionsSnapshot = { map: EMPTY_REACTIONS, me: null, error: null };

/** One conversation's reactions, live. `start()` opens the stream, reads, and learns who the viewer is; the returned
 *  function stops it. Works with useSyncExternalStore (`subscribe`, `getSnapshot`). */
export function createReactionStore(conv: string | null, api: ReactionsApiLike, opts: StoreOptions = {}) {
  const setTimer = opts.setTimer ?? ((f, ms) => setTimeout(f, ms));
  const clearTimer = opts.clearTimer ?? ((t) => clearTimeout(t as ReturnType<typeof setTimeout>));
  let state = initialSync();
  let me: Reactor | null = null;
  let error: string | null = null;
  let errorTimer: unknown = null;
  let snap: ReactionsSnapshot = EMPTY_SNAPSHOT;
  let alive = false;
  /** Bumped by every start(): an answer to a request made before it (a GET or PUT that resolves after a stop) is ignored. */
  let gen = 0;
  const listeners = new Set<() => void>();

  const emit = () => { snap = { map: viewMap(state, me), me, error }; listeners.forEach((l) => l()); };
  const fail = (text: string) => {
    error = text;
    if (errorTimer) clearTimer(errorTimer);
    errorTimer = setTimer(() => { error = null; errorTimer = null; emit(); }, opts.errorMs ?? 5000);
  };

  function read() {
    if (!conv) return;
    const b = beginRead(state);
    state = b.state;
    const g = gen;
    api.fetch().then((m) => { if (alive && g === gen) { state = finishRead(state, b.token, m); emit(); } },
      () => { if (alive && g === gen) state = abortRead(state, b.token); });
  }

  function send(ref: string, emoji: string, who: Reactor) {
    const g = gen;
    void api.put(ref, emoji).then((r) => {
      if (!alive || g !== gen) return;
      const out = settle(state, ref, r, who);
      state = out.state;
      if (out.error) { fail(out.error); read(); }
      emit();
      if (out.send) send(ref, out.send, who);
    });
  }

  function toggle(ref: string, emoji: string) {
    if (!conv || !alive) return;
    if (!me) {
      // The viewer is not known yet: nothing can be shown at once; the server's answer is applied when it comes.
      const g = gen;
      void api.put(ref, emoji).then((r) => {
        if (!alive || g !== gen) return;
        if (r.ok) { state = applyServer(state, r.change); if (r.change.reactor.kind === 'user') me = r.change.reactor; } else { fail(r.error); read(); }
        emit();
      });
      return;
    }
    state = tap(state, ref, emoji, me);
    const t = takeSend(state, ref, me);
    state = t.state;
    emit();
    if (t.send) send(ref, t.send, me);
  }

  function start(): () => void {
    if (!conv) return () => {};
    alive = true;
    gen++;
    const g = gen;                                   // timers of this run die with it, even if a clear is missed
    // Nothing from an earlier run is still out: no GET, no request, no wish waiting on one.
    state = { ...state, reads: [], buffer: [], intents: {} };
    error = null;
    let attempt = 0;
    let stable: unknown = null;
    let off: () => void = () => {};
    let retry: unknown = null;
    const open = () => {
      stable = setTimer(() => { if (g !== gen) return; stable = null; attempt = 0; }, opts.stableMs ?? 30000);     // open this long: healthy again
      off = api.subscribe({
        onChange: (c) => { if (alive && g === gen) { attempt = 0; state = applyServer(state, c); emit(); } },
        onReopen: () => { read(); },
        // The browser gave up on the stream: it is gone from the shared map; reconnect after a pause and read what was missed.
        // Waits 3s, 6s, 12s ... up to 60s between tries; back to 3s once a stream has stayed open or delivered a change.
        onClosed: () => {
          if (!alive || g !== gen || retry) return;      // a retry is already waiting
          if (stable) { clearTimer(stable); stable = null; }
          const wait = Math.min((opts.resubscribeMs ?? 3000) * 2 ** attempt, opts.resubscribeMaxMs ?? 60000);
          attempt++;
          retry = setTimer(() => { if (g !== gen) return; retry = null; if (alive) { off(); open(); read(); } }, wait);
        },
      });
    };
    open();
    read();
    void api.loadMe().then((m) => { if (alive && m) { me = m; emit(); } });
    return () => {
      alive = false;
      off();
      if (retry) clearTimer(retry);
      if (stable) clearTimer(stable);
      if (errorTimer) clearTimer(errorTimer);
    };
  }

  return {
    start, toggle,
    subscribe: (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; },
    getSnapshot: () => snap,
  };
}
