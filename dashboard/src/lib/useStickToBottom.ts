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
 *
 * WHILE A REPLY IS REVEALED word by word (lib/revealStore.ts) and the reader is at the bottom, the
 * scroller glides instead of jumping: each frame it moves a share of the way (an exponential follow, ~100 ms) to where
 * the last word SHOWN sits at the bottom edge, not the transparent words already laid out below it, so it follows
 * the words as they appear, a line at a time and smoothly, never snapping per word or per burst. Scrolled up, nothing
 * changes: the reader is never pulled down. Reduced motion: no glide, it keeps to the bottom at once.
 */
import { useCallback, useEffect, useRef, type RefObject } from 'react';
import { FOLLOW_TOLERANCE_PX, anchorShift, isFollowing, pickAnchor, type ScrollMetrics } from './threadKit/anchor';
import { reducedMotion, revealPending } from './revealStore';

/** The glide's time constant (ms): each frame closes 1 - e^(-dt/τ) of the way... */
export const GLIDE_TAU_MS = 100;
/** ...at most this fast (px per ms: 6 px a frame at 60 Hz), so a new bubble or a code block's frame showing at once
 *  is followed over a few frames, not in one jump. */
export const GLIDE_MAX_PX_PER_MS = 0.36;

/** One frame of the glide: the next scrollTop, from where it is toward `target` (never back up). At most a frame's
 *  worth of the top speed per frame, however long the frame was, so a stall is never made up in one jump. */
export function glideStep(current: number, target: number, dtMs: number): number {
  const d = target - current;
  if (d <= 0.5) return d > 0 ? target : current;
  const dt = Math.min(34, Math.max(1, dtMs));
  return current + Math.min(d * (1 - Math.exp(-dt / GLIDE_TAU_MS)), GLIDE_MAX_PX_PER_MS * Math.min(dt, 1000 / 60));
}

/** At the bottom, as the thread reads it (lib/threadKit/anchor.ts isFollowing). */
export function isAtBottom(el: ScrollMetrics, threshold = FOLLOW_TOLERANCE_PX): boolean {
  return isFollowing(el, threshold);
}

export function useStickToBottom(ref: RefObject<HTMLElement | null>, opts: { rows?: RefObject<HTMLElement | null> } = {}) {
  const atBottom = useRef(true);
  const anchor = useRef<{ el: Element; index: number; offset: number } | null>(null);
  const rowsRef = opts.rows;
  const ownTop = useRef<number | null>(null);   // where this hook's own last scroll left the scroller
  const glide = useRef<number | null>(null);   // the follow glide's next frame, while one runs

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
    // Gliding, a scroll down (or none) is still following; only one up leaves the bottom.
    if (glide.current !== null && atBottom.current && own !== null && el.scrollTop >= own - 1) return;
    atBottom.current = isAtBottom(el);
    if (!atBottom.current) capture();
  }, [ref, capture]);

  /** The thread changed: at the bottom, follow it; scrolled up, move by however far the anchor moved. Instant, so a
   *  smooth scroll's in-between positions never read as "scrolled up". */
  const follow = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    if (atBottom.current) {
      if (glide.current !== null) return;                          // the glide is on it
      if (!reducedMotion() && revealPending(el) !== null) {
        // The glide, a frame at a time, until it is at the bottom with no reveal under way (or the reader left).
        let last = performance.now();
        function step(now: number) {
          if (now === last) { glide.current = requestAnimationFrame(step); return; }   // once a frame
          const sc = ref.current;
          if (!sc || !atBottom.current) { glide.current = null; return; }
          const hidden = revealPending(sc);
          const target = Math.max(0, sc.scrollHeight - sc.clientHeight - (hidden ?? 0));
          const next = glideStep(sc.scrollTop, target, now - last);
          last = now;
          if (next !== sc.scrollTop) { sc.scrollTop = next; ownTop.current = sc.scrollTop; }
          // There, with nothing still to show: the next change (more words, a row) starts it again (`follow`).
          if (!hidden && Math.abs(target - sc.scrollTop) < 1) { glide.current = null; return; }
          glide.current = requestAnimationFrame(step);
        }
        glide.current = requestAnimationFrame(step);
        return;
      }
      el.scrollTo({ top: 1e9 });
      return;
    }
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
  useEffect(() => () => { if (glide.current !== null) cancelAnimationFrame(glide.current); glide.current = null; }, []);

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
