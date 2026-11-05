/**
 * useSwipeNav — swipe paging on a question card's parts area (lib/swipeNav.ts holds the rules). Touch and pen drags
 * (pointer events; the area and every part in it are `touch-action: pan-y pinch-zoom` (lib/swipeNav swipeTouch), so
 * vertical scroll and zoom stay the browser's)
 * and trackpad two-finger swipes (wheel deltaX). While a finger drags sideways the part follows it a little (the
 * element marked `data-swipe-track` inside the area is moved; nothing re-renders), then settles back as the parts
 * slide; with reduced motion nothing follows and the part just switches. `go` is only ever called with a step the
 * card's buttons could take (swipeStep).
 */
import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';
import {
  SWIPE, dragOffset, releaseVelocity, swipeAxis, swipeIntent, swipeStartOk, swipeStep, wheelDelta, wheelStart,
  wheelStep, type Sample, type SwipeDir,
} from '../lib/swipeNav';
import { motionReduced } from '../lib/motion';

/** The parts' slide, shared with the track's settle so the two move as one. */
export const SLIDE_MS = 420;
export const SLIDE_EASE = 'cubic-bezier(0.22, 0.61, 0.36, 1)';

const FIELD = 'input, textarea, select, [contenteditable]:not([contenteditable="false"])';

/** Whether anything between the target and the area scrolls sideways (a wide code block keeps its own swipe). */
function inHScroll(target: EventTarget | null, area: HTMLElement): boolean {
  for (let el = target instanceof Element ? target : null; el && el !== area; el = el.parentElement) {
    if (el.scrollWidth > el.clientWidth + 1) {
      const ox = getComputedStyle(el).overflowX;
      if (ox === 'auto' || ox === 'scroll') return true;
    }
  }
  return false;
}

export interface SwipeNav {
  at: number;
  count: number;
  /** Off while the card is sending, answered or dismissed. */
  enabled: boolean;
  go: (dir: SwipeDir) => void;
}

export function useSwipeNav(area: RefObject<HTMLElement | null>, nav: SwipeNav): void {
  const latest = useRef(nav);
  useLayoutEffect(() => { latest.current = nav; });
  const on = nav.enabled && nav.count > 1;

  useEffect(() => {
    const el = area.current;
    if (!el || !on) return;
    let g: { id: number; x0: number; y0: number; axis: 'h' | 'v' | null; samples: Sample[] } | null = null;
    let wheel = wheelStart;
    let unclick: (() => void) | null = null;

    const track = () => el.querySelector<HTMLElement>('[data-swipe-track]');
    const follow = (px: number) => {
      const t = track();
      if (!t) return;
      t.style.transition = 'none';
      t.style.transform = px ? `translateX(${px}px)` : '';
    };
    const settle = () => {
      const t = track();
      if (!t || !t.style.transform) return;
      t.style.transition = motionReduced() ? 'none' : `transform ${SLIDE_MS}ms ${SLIDE_EASE}`;
      t.style.transform = '';
    };
    const go = (dir: SwipeDir | null) => {
      const n = latest.current;
      const d = swipeStep(dir, n.at, n.count);
      if (d && n.enabled) n.go(d);
    };
    // The tap that ends a sideways drag is not a pick of the option under the finger.
    const swallowClick = () => {
      unclick?.();
      const stop = (e: Event) => { e.stopPropagation(); e.preventDefault(); unclick?.(); };
      el.addEventListener('click', stop, true);
      const timer = setTimeout(() => unclick?.(), 400);
      unclick = () => { el.removeEventListener('click', stop, true); clearTimeout(timer); unclick = null; };
    };

    const down = (e: PointerEvent) => {
      // A new touch is a new gesture: a drag that ended without its click must not eat this tap's.
      unclick?.();
      const t = e.target instanceof Element ? e.target : null;
      if (!swipeStartOk({ x: e.clientX, viewportWidth: window.innerWidth, pointerType: e.pointerType, primary: e.isPrimary,
        inField: !!t?.closest(FIELD), inHScroll: inHScroll(t, el) })) { g = null; return; }
      g = { id: e.pointerId, x0: e.clientX, y0: e.clientY, axis: null, samples: [{ x: e.clientX, y: e.clientY, t: e.timeStamp }] };
    };
    const move = (e: PointerEvent) => {
      if (!g || e.pointerId !== g.id) return;
      const dx = e.clientX - g.x0; const dy = e.clientY - g.y0;
      g.samples.push({ x: e.clientX, y: e.clientY, t: e.timeStamp });
      if (g.samples.length > 32) g.samples.splice(0, g.samples.length - 32);
      if (!g.axis) {
        g.axis = swipeAxis(dx, dy);
        if (g.axis === 'v') { g = null; return; }
        if (g.axis === 'h') { try { el.setPointerCapture(e.pointerId); } catch { /* already released */ } }
      }
      if (g.axis === 'h' && !motionReduced()) {
        const n = latest.current;
        follow(dragOffset(dx, n.at, n.count, el.clientWidth));
      }
    };
    const up = (e: PointerEvent) => {
      if (!g || e.pointerId !== g.id) return;
      const was = g; g = null;
      if (was.axis !== 'h') return;
      swallowClick();
      was.samples.push({ x: e.clientX, y: e.clientY, t: e.timeStamp });
      const dir = swipeIntent({ dx: e.clientX - was.x0, dy: e.clientY - was.y0, vx: releaseVelocity(was.samples) });
      settle();
      go(dir);
    };
    const cancel = (e: PointerEvent) => {
      if (!g || e.pointerId !== g.id) return;
      g = null;
      settle();
    };
    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey) return;   // a pinch on the trackpad: zoom
      const dx = wheelDelta(e.deltaX, e.deltaMode, el.clientWidth);
      const dy = wheelDelta(e.deltaY, e.deltaMode, el.clientHeight);
      const side = Math.abs(dx) > SWIPE.dominance * Math.abs(dy);
      if (side && inHScroll(e.target, el)) return;
      const r = wheelStep(wheel, { dx, dy, t: e.timeStamp });
      wheel = r.state;
      // A sideways swipe over the card is the card's: not the browser's back/forward.
      if (side) e.preventDefault();
      go(r.dir);
    };

    el.addEventListener('pointerdown', down);
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', cancel);
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => {
      el.removeEventListener('pointerdown', down);
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerup', up);
      el.removeEventListener('pointercancel', cancel);
      el.removeEventListener('wheel', onWheel);
      unclick?.();
      follow(0);
    };
  }, [area, on]);
}
