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
 * `rows`: the element whose children are the thread's rows, when it is not the scroller itself. A row holding its own
 * list of rows (marked data-anchor-rows) is anchored by the line showing inside it.
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
  const ownTop = useRef<number | null>(null);   // where this hook's own last scroll left the scroller

  /** Note the reader's anchor: the first row still showing, and its offset below the scroller's top edge. */
  const capture = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    // A row marked data-anchor-skip (the older-pages sentinel above the first row) is never the anchor: it stays at
    // the top while pages land under it.
    const rows = Array.from((rowsRef?.current ?? el).children).filter((r) => !r.hasAttribute('data-anchor-skip'));
    const top = el.getBoundingClientRect().top;
    const i = pickAnchor(rows.map((r) => r.getBoundingClientRect()), top);
    if (i < 0) { anchor.current = null; return; }
    // A row that is itself a list of rows (a call's card, marked data-anchor-rows) can grow at its top while the
    // reader is inside it (an older page brings more of the call): the line showing inside it is the anchor then.
    const inner = rows[i].querySelector('[data-anchor-rows]');
    const parts = inner ? Array.from(inner.children).filter((r) => !r.hasAttribute('data-anchor-skip')) : [];
    const j = parts.length ? pickAnchor(parts.map((r) => r.getBoundingClientRect()), top) : -1;
    const at = j >= 0 ? parts[j] : rows[i];
    anchor.current = { el: at, index: i, offset: at.getBoundingClientRect().top - top };
  }, [ref, rowsRef]);

  /** Put on the feed element: `<div ref={ref} onScroll={onScroll}>`. */
  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    // The scroll this hook made itself (holding the anchor) arrives as an event after the fact, by when the rows may
    // have changed again (more of an older page laid out): noting the anchor then would note it where that change
    // left it, before it is held back. The anchor is already noted for that scroll.
    const own = ownTop.current;
    ownTop.current = null;
    if (own !== null && Math.abs(el.scrollTop - own) < 1) return;
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
    const row = a.el.isConnected ? a.el
      : Array.from((rowsRef?.current ?? el).children).filter((r) => !r.hasAttribute('data-anchor-skip'))[a.index];
    if (!row) { capture(); return; }
    const shift = anchorShift(a.offset, row.getBoundingClientRect().top - el.getBoundingClientRect().top);
    if (shift) { el.scrollTop += shift; ownTop.current = el.scrollTop; }
    // The same row still there: held to the offset the reader left it at (noting it afresh after each correction
    // would carry each one's sub-pixel rounding into the next, a pixel or two over a page of changes).
    if (row === a.el) {
      const i = Array.from((rowsRef?.current ?? el).children).filter((r) => !r.hasAttribute('data-anchor-skip')).findIndex((r) => r.contains(row));
      anchor.current = { ...a, index: i };
    } else capture();
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
