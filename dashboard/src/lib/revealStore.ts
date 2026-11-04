/**
 * revealStore.ts — the reveal state of every streamed reply, kept apart from its drawing (components/kit/StreamMarkdown.tsx),
 * so it survives the drawing being replaced: the live overlay's bubble handing off to the finished row, a block that
 * moves from growing to finished. Keyed by the reply's key (a live block's id, an Arturo turn's key) and by the key
 * the finished message will have (a transcript block's uuid, aliased once the stream knows it).
 *
 * One requestAnimationFrame loop for every reply: each frame it asks each reply's pacer (lib/reveal.ts) what is due
 * and switches those units on (a class: their CSS opacity fade). Nothing re-renders per word. A drawing mounted later
 * gives units already released their class at once, a fade still under way carried on where it was (a negative
 * animation delay), so nothing shown fades again or flickers; units still queued go on at the pace (fast once the
 * stream is done). Reduced motion, or a reply scrolled out of view: everything is shown at once.
 *
 * Old messages never have an entry, so they are drawn as before (components/kit/Markdown.tsx) and never animate.
 */
import { FADE_MS, RevealPacer } from './reveal';
import { StreamPlan } from './threadKit/streamMarkdown';

/** Each reveal frame's own work, kept in window.__orchestraReveal so a performance check can read it (shared with
 *  components/kit/RevealText.tsx). */
export interface RevealStats { frames: number; maxMs: number; over16: number }
export function revealStats(): RevealStats {
  const w = window as unknown as { __orchestraReveal?: RevealStats };
  return (w.__orchestraReveal ||= { frames: 0, maxMs: 0, over16: 0 });
}

export const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

const ON = 'is-on';
const SHOWN = 'is-shown';

export class RevealEntry {
  plan = new StreamPlan();
  readonly pacer = new RevealPacer();
  /** Bumped when the text started over: the drawing starts over too. */
  gen = 0;
  done = false;
  root: HTMLElement | null = null;
  /** Each unit's elements in the drawing (a word; a box that fades with its first word). */
  private els: Element[][] = [];
  private away = false;
  private io: IntersectionObserver | null = null;
  readonly id: string;
  constructor(id: string) { this.id = id; }

  /** In render: the text so far. A text that does not extend the old one starts over. */
  read(text: string, done: boolean): StreamPlan {
    const t = text.replace(/^\s+/, '');
    if (!this.plan.update(t, done)) {
      this.plan = new StreamPlan();
      this.plan.update(t, done);
      this.pacer.reset();
      this.gen++;
    }
    this.done = done;
    return this.plan;
  }

  /** The words drawn so far (to check that a finished message is this reply). */
  drawnText(): string { return this.plan.text.slice(0, this.plan.safe); }

  attach(root: HTMLElement): void {
    this.root = root;
    attached.add(this);
    this.away = false;
    // Scrolled away: more than a screen of the scroller from its view (a reply just below the bottom edge, about to
    // be followed into view, is not away).
    if (typeof IntersectionObserver !== 'undefined') {
      this.io = new IntersectionObserver((list) => {
        const e = list[list.length - 1];
        this.away = !e.isIntersecting;
        if (this.away) this.flushNow();
      }, { root: scrollerOf(root), rootMargin: '100% 0px 100% 0px' });
      this.io.observe(root);
    }
  }

  detach(root: HTMLElement): void {
    if (this.root !== root) return;
    this.io?.disconnect(); this.io = null;
    this.root = null; this.els = [];
    attached.delete(this);
  }

  /** After each commit: find the units drawn, give those already released their class, and go on releasing. */
  sync(): void {
    const root = this.root;
    if (!root) return;
    const els: Element[][] = [];
    const box = root.closest('[data-ru-root]');
    if (box && box.hasAttribute('data-ru')) (els[Number(box.getAttribute('data-ru'))] ||= []).push(box);
    root.querySelectorAll('[data-ru]').forEach((el) => { (els[Number(el.getAttribute('data-ru'))] ||= []).push(el); });
    this.els = els;
    this.pacer.setKnown(this.plan.units, this.done);
    if (this.away || reducedMotion()) this.pacer.flush();
    const now = performance.now();
    for (let i = 0; i < this.pacer.released; i++) {
      for (const el of els[i] ?? []) if (!el.classList.contains(ON) && !el.classList.contains(SHOWN)) this.light(el, i, now);
    }
    if (!this.settled()) wake(this);
  }

  /** One frame: release what is due. False once everything is shown and the stream is done. */
  step(now: number): boolean {
    if (!this.root) return false;                     // not drawn: paused until a drawing syncs it again
    if (this.away || reducedMotion()) this.pacer.flush();
    const from = this.pacer.released;
    this.pacer.tick(now);
    for (let i = from; i < this.pacer.released; i++) for (const el of this.els[i] ?? []) this.light(el, i, now);
    return !this.settled();
  }

  settled(): boolean { return this.done && this.pacer.released >= this.pacer.known; }

  /** Inside the scroller: how far the words drawn but not shown yet reach below the last word shown (px). */
  hiddenBelow(): number {
    const { released, known } = this.pacer;
    if (!this.root || released >= known) return 0;
    const last = this.els[known - 1]?.at(-1);
    if (!last) return 0;
    const shown = released > 0 ? this.els[released - 1]?.at(-1)?.getBoundingClientRect().bottom : this.root.getBoundingClientRect().top;
    return shown === undefined ? 0 : Math.max(0, last.getBoundingClientRect().bottom - shown);
  }

  private flushNow(): void {
    this.pacer.flush();
    for (let i = 0; i < this.pacer.released; i++) for (const el of this.els[i] ?? []) if (!el.classList.contains(ON)) el.classList.add(SHOWN);
  }

  /** Switch a unit's element on: a fresh release fades from 0; one released a while ago carries on its fade where it
   *  is (or is simply shown, past the fade). */
  private light(el: Element, i: number, now: number): void {
    const age = now - (this.pacer.releasedAt[i] ?? -Infinity);
    if (!(age < FADE_MS) || reducedMotion()) { el.classList.add(SHOWN); return; }
    if (age > 1) (el as HTMLElement).style.animationDelay = `${-Math.round(age)}ms`;
    el.classList.add(ON);
  }
}

/** The element that scrolls this one (null: the page). */
function scrollerOf(el: Element): Element | null {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const o = getComputedStyle(p).overflowY;
    if (o === 'auto' || o === 'scroll') return p;
  }
  return null;
}

// ---- The store ------------------------------------------------------------------------------------------------------

const MAX_ENTRIES = 80;
const byKey = new Map<string, RevealEntry>();
const attached = new Set<RevealEntry>();

/** The entry for a reply being written (created when new). */
export function revealEntry(key: string): RevealEntry {
  let e = byKey.get(key);
  if (!e) {
    e = new RevealEntry(key);
    byKey.set(key, e);
    if (byKey.size > MAX_ENTRIES) {
      for (const [k, old] of byKey) { if (byKey.size <= MAX_ENTRIES) break; if (!old.root) byKey.delete(k); }
    }
  }
  return e;
}

/** The key a finished message will be found by (a live block's transcript uuid). */
export function aliasReveal(key: string, entry: RevealEntry): void {
  if (byKey.get(key) !== entry) byKey.set(key, entry);
}

/** A finished message's reveal, if it was streamed here: by its key, or by the uuid of a transcript row's key
 *  ("<uuid>:<n>"), and only when its words start with the words that were drawn. */
export function findReveal(key: string | undefined, text?: string): RevealEntry | undefined {
  if (!key) return undefined;
  const e = byKey.get(key) ?? byKey.get(key.replace(/:\d+$/, ''));
  if (!e) return undefined;
  if (text !== undefined && !text.replace(/^\s+/, '').startsWith(e.drawnText().trimEnd())) return undefined;
  return e;
}

/** Whether a reply is being revealed inside this element, and how far its hidden words reach below the last shown
 *  (lib/useStickToBottom follows the words as they show, not the space they are drawn in). Null when none is. */
export function revealPending(scroller: Element): number | null {
  let out: number | null = null;
  for (const e of attached) {
    if (!e.root || !scroller.contains(e.root) || e.settled()) continue;
    out = Math.max(out ?? 0, e.hiddenBelow());
  }
  return out;
}

// ---- The loop -------------------------------------------------------------------------------------------------------

const live = new Set<RevealEntry>();
let raf = 0;

function frame(now: number): void {
  const t0 = performance.now();
  for (const e of live) if (!e.step(now)) live.delete(e);
  const took = performance.now() - t0;
  const stats = revealStats();
  stats.frames++; if (took > stats.maxMs) stats.maxMs = took; if (took > 16) stats.over16++;
  raf = live.size ? requestAnimationFrame(frame) : 0;
}

function wake(e: RevealEntry): void {
  live.add(e);
  if (!raf) raf = requestAnimationFrame(frame);
}
