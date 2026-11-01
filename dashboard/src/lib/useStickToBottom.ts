/**
 * useStickToBottom — every thread's scroller (Arturo's page, his floating pane, every agent's page): it follows new
 * messages only while the reader is AT the bottom, and otherwise keeps what the reader is looking at exactly where
 * it is while the thread changes (lib/threadKit/anchor.ts holds the rule).
 *
 * At the bottom: what arrives pushes the older messages up. Scrolled up, even a line: the first row still showing at
 * the top is the anchor, and after any change (new rows below or above, a reply still streaming, a card settling,
 * an image loading, all caught by `follow` from the view and by a ResizeObserver here) the scroller moves by however
 * far that row moved. The browser's own scroll anchoring is switched off on the scroller, so the two never correct
 * the same change twice, and it holds the same on every browser (Safari has none of its own). Sending, or opening a
 * thread, jumps back to the bottom (`toBottom`); jumping to a card somewhere up the thread first stops following
 * (`hold`).
 *
 * `rows`: the element whose children are the thread's rows, when it is not the scroller itself.
 */
import { useCallback, useEffect, useRef, type RefObject } from 'react';
import { FOLLOW_TOLERANCE_PX, anchorShift, isFollowing, pickAnchor, type ScrollMetrics } from './threadKit/anchor';

/** At the bottom, as the thread reads it (lib/threadKit/anchor.ts isFollowing). */
export function isAtBottom(el: ScrollMetrics, threshold = FOLLOW_TOLERANCE_PX): boolean {
  return isFollowing(el, threshold);
}

export function useStickToBottom(ref: RefObject<HTMLElement | null>, opts: { rows?: RefObject<HTMLElement | null> } = {}) {
  const atBottom = useRef(true);
  const anchor = useRef<{ el: Element; index: number; offset: number } | null>(null);
  const rowsRef = opts.rows;

  /** Note the reader's anchor: the first row still showing, and its offset below the scroller's top edge. */
  const capture = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const rows = Array.from((rowsRef?.current ?? el).children);
    const top = el.getBoundingClientRect().top;
    const i = pickAnchor(rows.map((r) => r.getBoundingClientRect()), top);
    anchor.current = i >= 0 ? { el: rows[i], index: i, offset: rows[i].getBoundingClientRect().top - top } : null;
  }, [ref, rowsRef]);

  /** Put on the feed element: `<div ref={ref} onScroll={onScroll}>`. */
  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    atBottom.current = isAtBottom(el);
    if (!atBottom.current) capture();
  }, [ref, capture]);

  /** The thread changed: at the bottom, follow it; scrolled up, move by however far the anchor moved. Instant, so a
   *  smooth scroll's in-between positions never read as "scrolled up". */
  const follow = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    if (atBottom.current) { el.scrollTo({ top: 1e9 }); return; }
    const a = anchor.current;
    if (!a) { capture(); return; }
    // A thread re-read in full draws its rows anew: the row now in the anchor's place stands in for it.
    const row = a.el.isConnected ? a.el : (rowsRef?.current ?? el).children[a.index];
    if (!row) { capture(); return; }
    const shift = anchorShift(a.offset, row.getBoundingClientRect().top - el.getBoundingClientRect().top);
    if (shift) el.scrollTop += shift;
    capture();
  }, [ref, rowsRef, capture]);

  /** The reader acted (sent, opened, switched): back to the bottom, and following again. */
  const toBottom = useCallback(() => {
    atBottom.current = true;
    ref.current?.scrollTo({ top: 1e9 });
  }, [ref]);

  /** About to bring something up the thread into view (a card the bell points at): stop following. */
  const hold = useCallback(() => {
    atBottom.current = false;
    capture();
  }, [capture]);

  // Changes the view does not announce (a reply's height settling, an image, a card that opens, the column
  // resizing) are caught here; the browser's own anchoring stays out of it.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.overflowAnchor = 'none';
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => follow());
    ro.observe(el);
    // Each row too (a row that grows inside a scroller that is its own row list resizes nothing else), and each
    // row as it arrives.
    const rows = rowsRef?.current ?? el;
    if (rows !== el) ro.observe(rows);
    Array.from(rows.children).forEach((c) => ro.observe(c));
    const mo = typeof MutationObserver !== 'undefined'
      ? new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => { if (n instanceof Element) ro.observe(n); })))
      : null;
    mo?.observe(rows, { childList: true });
    return () => { ro.disconnect(); mo?.disconnect(); };
  }, [follow]); // eslint-disable-line react-hooks/exhaustive-deps -- the refs are read once the elements exist; `follow` already changes with them

  return { onScroll, follow, toBottom, hold };
}
