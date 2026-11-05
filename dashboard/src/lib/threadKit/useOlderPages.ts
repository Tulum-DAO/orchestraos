/**
 * useOlderPages — scrolling up through a thread's history: the live window (the newest items, kept current by the
 * page's own stream or poll) with older pages added above it as the reader nears the top (lib/threadKit/paging.ts
 * joins them: no item twice, no gap, the live copy of what both hold).
 *
 * `load(cursor)` reads the page just older than `cursor` (null: the first, read from `firstCursor` of the list) and
 * answers { items, hasOlder, next }. One load at a time; none once the start of the thread is reached or after a
 * refusal. A new conversation (`resetKey`, or a live window that no longer meets the list) starts over.
 *
 * What the reader sees does not move when a page lands: the rows are added above the thread scroller's anchor
 * (lib/useStickToBottom.ts), which holds it to the pixel. `useNearTop` fires the next load a screen or two before the
 * reader gets there.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { joinHead, joinTail, type KeyOf } from './paging';

/** How the list so far and the live window are joined (paging.ts joinTail, or joinBySeq for a numbered thread). */
export type JoinLive<T> = (list: T[], live: T[]) => { items: T[]; reset: boolean };

export interface OlderPage<T> { items: T[]; hasOlder: boolean; next: string | null }

export function useOlderPages<T>({ live, keyOf, firstCursor, load, resetKey, enabled = true, join }: {
  live: T[];
  keyOf: KeyOf<T>;
  /** Default: joinTail by `keyOf`. */
  join?: JoinLive<T>;
  firstCursor: (items: T[]) => string | null;
  load: (cursor: string) => Promise<OlderPage<T>>;
  resetKey: string;
  enabled?: boolean;
}) {
  // Everything known that is older than (or overlaps) the live window (the live window is laid over it on read), and
  // where paging stands, all kept with the conversation they belong to: on the render that opens another one, none
  // of it applies (a numbered thread's turns would otherwise join the next thread's by number).
  type Pages = { key: string; head: T[]; hasOlder: boolean; loading: boolean; cursor: string | null };
  const fresh = (key: string): Pages => ({ key, head: [], hasOlder: true, loading: false, cursor: null });
  const [stored, setPages] = useState<Pages>(() => fresh(resetKey));
  const pages = stored.key === resetKey ? stored : fresh(resetKey);
  const busy = useRef<string | null>(null);              // the conversation a load is running for
  const liveRef = useRef(live);
  const pagesRef = useRef(pages);
  const resetRef = useRef(resetKey);
  useLayoutEffect(() => { liveRef.current = live; pagesRef.current = pages; resetRef.current = resetKey; });
  /** A change to this conversation's paging (another conversation's state is replaced, not changed). */
  const update = useCallback((key: string, f: (p: Pages) => Pages) =>
    setPages((p) => (key !== resetRef.current ? p : f(p.key === key ? p : fresh(key)))), []); // eslint-disable-line react-hooks/exhaustive-deps

  const joinLive = useCallback((list: T[], win: T[]) => (join ? join(list, win) : joinTail(list, win, keyOf)), [join, keyOf]);
  const head = pages.head;
  const joined = useMemo(() => joinLive(head, live), [head, live, joinLive]);
  // The live window moves down as items arrive: keep what it moved past, so no gap opens above it. A live window
  // that no longer meets the list is another conversation: the pages go.
  useEffect(() => {
    if (!head.length) return;
    if (joined.reset) { update(resetKey, () => fresh(resetKey)); return; }
    if (joined.items.length !== head.length || joined.items[joined.items.length - 1] !== head[head.length - 1]) {
      update(resetKey, (p) => ({ ...p, head: joined.items }));
    }
  }, [joined]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadOlder = useCallback(async () => {
    const key = resetKey;
    const p = pagesRef.current;
    if (!enabled || busy.current === key || !p.hasOlder || p.key !== key) return;
    const from = p.cursor ?? firstCursor(joinLive(p.head, liveRef.current).items);
    // Nothing in the window a cursor can name yet (still loading, or only turns not stored yet): nothing to read now;
    // the next change to the list checks again (useNearTop's `watch`).
    if (!from) return;
    busy.current = key;
    update(key, (q) => ({ ...q, loading: true }));
    try {
      const page = await load(from);
      update(key, (q) => ({ ...q, head: joinHead(page.items, joinLive(q.head, liveRef.current).items, keyOf),
        cursor: page.next, hasOlder: page.hasOlder && !!page.next, loading: false }));
    } catch {
      update(key, (q) => ({ ...q, hasOlder: false, loading: false }));
    } finally {
      if (busy.current === key) busy.current = null;
    }
  }, [enabled, keyOf, joinLive, firstCursor, load, resetKey, update]);

  return { items: joined.items, hasOlder: pages.hasOlder, loading: pages.loading, loadOlder };
}

/** Calls `onNear` while `sentinel` (an element above the thread's first row) is within a screen and a half of the
 *  scroller's top edge, so the next page is read before the reader reaches it; checked again whenever `watch`
 *  changes (a page landed and the sentinel may still be in range). */
export function useNearTop(scroller: RefObject<HTMLElement | null>, sentinel: RefObject<HTMLElement | null>,
  onNear: () => void, watch: unknown, enabled = true) {
  const latest = useRef(onNear);
  useLayoutEffect(() => { latest.current = onNear; });
  useEffect(() => {
    const root = scroller.current, el = sentinel.current;
    if (!enabled || !root || !el || typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) latest.current(); },
      { root, rootMargin: '150% 0px 0px 0px', threshold: 0 });
    io.observe(el);
    return () => io.disconnect();
  }, [scroller, sentinel, watch, enabled]);
}
