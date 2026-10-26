/**
 * useStickToBottom — a live feed that follows new messages only while you are AT the bottom.
 *
 * The Arturo feeds used to scroll to the bottom on every new turn and every live caption, so scrolling up
 * during a call was pulled straight back down. Now: at the bottom (within `threshold` px), new messages push the
 * older ones up as before; scrolled up, the feed stays where you are. Sending, or opening a thread, jumps back
 * to the bottom (`toBottom`).
 */
import { useCallback, useRef, type RefObject } from 'react';

export function isAtBottom(el: { scrollHeight: number; scrollTop: number; clientHeight: number }, threshold = 80): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;
}

export function useStickToBottom(ref: RefObject<HTMLElement | null>, threshold = 80) {
  const atBottom = useRef(true);
  /** Put on the feed element: `<div ref={ref} onScroll={onScroll}>`. */
  const onScroll = useCallback(() => {
    const el = ref.current;
    if (el) atBottom.current = isAtBottom(el, threshold);
  }, [ref, threshold]);
  /** New content arrived: follow it only if the reader was at the bottom. Instant, so a smooth scroll's
   *  in-between positions never read as "scrolled up". */
  const follow = useCallback(() => {
    if (atBottom.current) ref.current?.scrollTo({ top: 1e9 });
  }, [ref]);
  /** The reader acted (sent, opened, switched): back to the bottom, and following again. */
  const toBottom = useCallback(() => {
    atBottom.current = true;
    ref.current?.scrollTo({ top: 1e9 });
  }, [ref]);
  return { onScroll, follow, toBottom };
}
