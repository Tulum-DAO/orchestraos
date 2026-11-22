/**
 * "Is this element near what the person can see?" for media that should not load until it is
 * (components/kit/ChatImages.tsx: a video tile fetches nothing until then).
 *
 * The chat threads scroll inside their own containers, not the page. An IntersectionObserver with
 * the implicit root (the viewport) clips at that container's edge, and its rootMargin does not grow
 * the container's clip, so a margin there gives no head start inside a thread. So the root is the
 * nearest SCROLLING ancestor (overflow-y auto/scroll/overlay), and the margin is applied around it;
 * with no such ancestor the root is the viewport. Pure apart from the observer and the style lookup,
 * both injectable (tests run without a browser).
 */

/** How close (px) to the scrolling container's visible edge counts as near. */
export const NEAR_PX = 400;

interface Node { parentElement: Node | null }
type StyleOf = (el: Node) => { overflowY?: string } | null | undefined;
type IOCtor = new (cb: (entries: Array<{ isIntersecting: boolean }>) => void, opts: { root: Node | null; rootMargin: string }) => {
  observe: (el: Node) => void;
  disconnect: () => void;
};

/** The nearest ancestor of `el` that scrolls vertically, or null (the viewport). */
export function scrollRoot<T extends Node>(el: T, styleOf: StyleOf): T | null {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const o = styleOf(p)?.overflowY;
    if (o === 'auto' || o === 'scroll' || o === 'overlay') return p as T;
  }
  return null;
}

export interface NearEnv { IO?: IOCtor; styleOf?: StyleOf; margin?: number }

/**
 * Call `onNear` once, when `el` comes within NEAR_PX of its scrolling container's visible area (or the
 * viewport's). Returns the cleanup (the observer is disconnected: on unmount, and right after the
 * first hit). Without an IntersectionObserver, `onNear` runs at once.
 */
export function watchNear(el: Node, onNear: () => void, env: NearEnv = {}): () => void {
  const IO = env.IO ?? (typeof IntersectionObserver === 'undefined' ? undefined : (IntersectionObserver as unknown as IOCtor));
  if (!IO) { onNear(); return () => {}; }
  const styleOf: StyleOf = env.styleOf ?? ((n) => getComputedStyle(n as unknown as Element));
  let done = false;
  const io = new IO((entries) => {
    if (done || !entries.some((e) => e.isIntersecting)) return;
    done = true;
    io.disconnect();
    onNear();
  }, { root: scrollRoot(el, styleOf), rootMargin: `${env.margin ?? NEAR_PX}px` });
  io.observe(el);
  return () => { done = true; io.disconnect(); };
}
