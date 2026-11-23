/**
 * The inline video tile (components/kit/ChatImages.tsx VideoThumb, through ImageStrip): muted, controls, plays
 * inline, and NEVER autoplays (a thread full of recordings must not start playing, or make a sound, by itself). It is
 * lazy: until the tile is near the thread's visible area it has no source and preload "none" (lib/nearViewport.ts
 * watchNear, tested here with a fake IntersectionObserver: no browser).
 *
 * The real component is rendered (react-dom/server). Its stylesheet is stood in by an empty module (a loader hook),
 * and the props each <video> is created with are read from React.createElement on the global React (the component
 * compiles to it), since server markup does not carry `muted` (React sets it as a property).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { register } from 'node:module';
import * as React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { NEAR_PX, scrollRoot, watchNear } from './nearViewport';

register('data:text/javascript,' + encodeURIComponent(
  "export async function load(url, ctx, next) { return url.endsWith('.css') ? { format: 'module', source: '', shortCircuit: true } : next(url, ctx); }"));

let videos: Record<string, unknown>[] = [];
const spy = (type: unknown, props: Record<string, unknown> | null, ...kids: unknown[]) => {
  if (type === 'video') videos.push({ ...(props || {}) });
  return (React.createElement as (...a: unknown[]) => React.ReactElement)(type, props, ...kids);
};
(globalThis as { React?: unknown }).React = { ...React, createElement: spy };

const { ImageStrip } = await import('../components/kit/ChatImages.tsx');

const ID = 'ab'.repeat(16);
const video = { id: ID, path: '/srv/p/demo.mp4', name: 'demo.mp4', kind: 'video' as const };

function render(): { html: string; props: Record<string, unknown> } {
  videos = [];
  const html = renderToStaticMarkup(React.createElement(ImageStrip, { images: [video] }));
  assert.equal(videos.length, 1, 'one <video> for one video ref');
  return { html, props: videos[0] };
}

function assertQuiet(props: Record<string, unknown>, html: string) {
  assert.equal(props.muted, true, 'muted');
  assert.equal(props.autoPlay, undefined, 'no autoPlay prop');
  assert.equal(props.autoplay, undefined);
  assert.doesNotMatch(html, /autoplay/i, 'no autoplay attribute');
  assert.equal(props.controls, true);
  assert.equal(props.playsInline, true);
}

/** A stand-in IntersectionObserver: records its options, what it observes, and whether it was disconnected. */
function fakeIO() {
  const made: Array<{ cb: (e: Array<{ isIntersecting: boolean }>) => void; opts: { root: unknown; rootMargin: string }; observed: unknown[]; disconnected: number }> = [];
  class IO {
    rec: (typeof made)[number];
    constructor(cb: (e: Array<{ isIntersecting: boolean }>) => void, opts: { root: unknown; rootMargin: string }) {
      this.rec = { cb, opts, observed: [], disconnected: 0 };
      made.push(this.rec);
    }
    observe(el: unknown) { this.rec.observed.push(el); }
    disconnect() { this.rec.disconnected++; }
  }
  return { IO, made };
}

/** A tile inside a thread: page > scroller (overflow-y auto) > row > tile. */
function thread() {
  const page = { parentElement: null, overflowY: 'visible' };
  const scroller = { parentElement: page, overflowY: 'auto' };
  const row = { parentElement: scroller, overflowY: 'visible' };
  const tile = { parentElement: row, overflowY: 'visible' };
  const styleOf = (n: { overflowY?: string }) => ({ overflowY: n.overflowY });
  return { page, scroller, tile, styleOf };
}

test('SABOTAGE tile: the inline video tile is muted and never autoplays', () => {
  const { html, props } = render();
  assertQuiet(props, html);
});

test('SABOTAGE lazy: before it is near, the tile has no source and preload none; without IntersectionObserver it loads at once', () => {
  const g = globalThis as { IntersectionObserver?: unknown };
  g.IntersectionObserver = fakeIO().IO;
  try {
    const { html, props } = render();
    assert.equal(props.src, undefined, 'no source off screen');
    assert.equal(props.preload, 'none');
    assert.doesNotMatch(html, /src=/);
    assertQuiet(props, html);
  } finally { delete g.IntersectionObserver; }
  const { props } = render();
  assert.equal(props.preload, 'metadata');
  assert.equal(props.src, `/api/media/${ID}`);
});

test('SABOTAGE lazy: watchNear observes the tile against the thread\'s scroller with a NEAR_PX margin, loads once when near, disconnects', () => {
  const { scroller, tile, styleOf } = thread();
  const { IO, made } = fakeIO();
  let near = 0;
  const stop = watchNear(tile, () => { near++; }, { IO, styleOf });
  assert.equal(made.length, 1);
  assert.equal(made[0].opts.root, scroller, 'the root is the scrolling container, so the margin works inside the thread');
  assert.equal(made[0].opts.rootMargin, `${NEAR_PX}px`);
  assert.equal(NEAR_PX, 400);
  assert.deepEqual(made[0].observed, [tile]);
  made[0].cb([{ isIntersecting: false }]);
  assert.equal(near, 0, 'not near yet: nothing loads');
  made[0].cb([{ isIntersecting: true }]);
  assert.equal(near, 1, 'near: it loads');
  assert.ok(made[0].disconnected >= 1, 'the observer is let go after the first hit');
  made[0].cb([{ isIntersecting: true }]);
  assert.equal(near, 1, 'once');
  stop();
});

test('SABOTAGE lazy: unmounting before the tile is near disconnects the observer and nothing loads later', () => {
  const { tile, styleOf } = thread();
  const { IO, made } = fakeIO();
  let near = 0;
  const stop = watchNear(tile, () => { near++; }, { IO, styleOf });
  stop();
  assert.equal(made[0].disconnected, 1, 'unmount disconnects');
  made[0].cb([{ isIntersecting: true }]);
  assert.equal(near, 0, 'a late callback after unmount loads nothing');
});

test('scrollRoot: the nearest scrolling ancestor, else the viewport (null); no IntersectionObserver loads at once', () => {
  const { scroller, tile, styleOf } = thread();
  assert.equal(scrollRoot(tile, styleOf), scroller);
  assert.equal(scrollRoot(tile, () => ({ overflowY: 'visible' })), null);
  assert.equal(scrollRoot({ parentElement: { parentElement: null } }, () => ({ overflowY: 'scroll' })) !== null, true);
  let near = 0;
  watchNear(tile, () => { near++; }, { styleOf });   // node has no IntersectionObserver
  assert.equal(near, 1);
});

// ── the tile's effect, run as itself ──

/**
 * The #601 delta review found the lazy wiring untested: the component test above renders on the server, where effects
 * never run, so a VideoThumb whose effect never calls watchNear (or never returns its cleanup) kept the suite green.
 * This harness renders the element tree itself under React 19's hook dispatcher (as liveSwitch.test.ts does for a
 * hook): a function component gets its own hook slots per position, a host element's object ref is set to the node
 * `hostNode` gives it (as commit would), effects run after each render when their deps change (cleanup first), and a
 * state change from outside (the observer's callback) re-renders on `flush`. Icons (forwardRef) are leaves.
 */
type Eff = { deps?: unknown[]; cleanup?: (() => void) | void; run?: () => (() => void) | void };
type Inst = { slots: unknown[]; at: number };
type Host = { type: string; props: Record<string, unknown> };
function mountTree(make: () => React.ReactElement, hostNode: (type: string, props: Record<string, unknown>) => object) {
  const internals = (React as unknown as Record<string, { H: unknown }>).__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
  const insts = new Map<string, Inst>();
  let cur: Inst = { slots: [], at: 0 };
  let dirty = false;
  let mounted = true;
  let hosts: Host[] = [];
  const same = (a?: unknown[], b?: unknown[]) => !!a && !!b && a.length === b.length && a.every((x, i) => Object.is(x, b[i]));
  const dispatcher = {
    useState(init: unknown) {
      const inst = cur;
      const k = inst.at++;
      if (!(k in inst.slots)) inst.slots[k] = typeof init === 'function' ? (init as () => unknown)() : init;
      const set = (v: unknown) => {
        if (!mounted) return;
        const next = typeof v === 'function' ? (v as (p: unknown) => unknown)(inst.slots[k]) : v;
        if (!Object.is(next, inst.slots[k])) { inst.slots[k] = next; dirty = true; }
      };
      return [inst.slots[k], set];
    },
    useRef(v: unknown) { const k = cur.at++; if (!(k in cur.slots)) cur.slots[k] = { current: v }; return cur.slots[k]; },
    useEffect(fn: () => (() => void) | void, deps?: unknown[]) {
      const k = cur.at++;
      const prev = cur.slots[k] as Eff | undefined;
      if (prev && same(prev.deps, deps)) return;
      cur.slots[k] = { deps, cleanup: prev?.cleanup, run: fn } as Eff;
    },
    useCallback(fn: unknown, deps: unknown[]) {
      const k = cur.at++;
      const prev = cur.slots[k] as { fn: unknown; deps: unknown[] } | undefined;
      if (prev && same(prev.deps, deps)) return prev.fn;
      cur.slots[k] = { fn, deps };
      return fn;
    },
    useMemo(fn: () => unknown, deps: unknown[]) {
      const k = cur.at++;
      const prev = cur.slots[k] as { v: unknown; deps: unknown[] } | undefined;
      if (prev && same(prev.deps, deps)) return prev.v;
      const v = fn();
      cur.slots[k] = { v, deps };
      return v;
    },
  };
  const visit = (node: unknown, path: string): void => {
    if (node == null || typeof node !== 'object') return;
    if (Array.isArray(node)) { node.forEach((c, i) => visit(c, `${path}/${(c as { key?: unknown })?.key ?? i}`)); return; }
    const { type, props } = node as { type: unknown; props: Record<string, unknown> | undefined };
    if (typeof type === 'string') {
      hosts.push({ type, props: props ?? {} });
      const ref = props?.ref as { current: unknown } | undefined;
      if (ref && typeof ref === 'object') ref.current = hostNode(type, props ?? {});
      visit(props?.children, `${path}/${type}`);
      return;
    }
    if (typeof type === 'function') {
      const key = `${path}/${type.name}`;
      let inst = insts.get(key);
      if (!inst) { inst = { slots: [], at: 0 }; insts.set(key, inst); }
      const prev = cur;
      cur = inst;
      inst.at = 0;
      let out: unknown;
      try { out = (type as (p: unknown) => unknown)(props); } finally { cur = prev; }
      visit(out, key);
      return;
    }
    if (type && typeof type === 'object') return;      // forwardRef (an icon): a leaf
    visit(props?.children, `${path}/frag`);           // a fragment
  };
  const flushEffects = () => {
    for (const inst of insts.values()) {
      for (const s of inst.slots) {
        const e = s as Eff | undefined;
        if (e && typeof e === 'object' && 'run' in e && e.run) {
          const fn = e.run; e.run = undefined;
          if (typeof e.cleanup === 'function') e.cleanup();
          e.cleanup = fn();
        }
      }
    }
  };
  const render = () => {
    const prev = internals.H;
    let n = 0;
    do {
      internals.H = dispatcher;
      try {
        do { dirty = false; hosts = []; visit(make(), ''); } while (dirty && ++n < 25);
      } finally { internals.H = prev; }
      flushEffects();
    } while (dirty && ++n < 25);
    assert.ok(n < 25, 'render settles');
  };
  render();
  return {
    /** The one <video>'s props as last rendered. */
    get video() { const v = hosts.filter((h) => h.type === 'video'); assert.equal(v.length, 1); return v[0].props; },
    get tileNear() { return hosts.find((h) => h.type === 'div' && h.props['data-chat-image'] === 'video')?.props['data-near']; },
    /** Something outside rendering happened (the observer called back): render again if it changed, as React would. */
    flush() { if (dirty) render(); },
    unmount() {
      for (const inst of insts.values()) for (const s of inst.slots) { const e = s as Eff | undefined; if (e && typeof e === 'object' && typeof e.cleanup === 'function') e.cleanup(); }
      mounted = false;
    },
  };
}

/** The tile mounted in a thread, with the page's IntersectionObserver and getComputedStyle stood in. */
function mountTile() {
  const t = thread();
  const { IO, made } = fakeIO();
  const g = globalThis as { IntersectionObserver?: unknown; getComputedStyle?: unknown };
  const prev = { IO: g.IntersectionObserver, gcs: g.getComputedStyle };
  g.IntersectionObserver = IO;
  g.getComputedStyle = t.styleOf;
  const restore = () => { g.IntersectionObserver = prev.IO; g.getComputedStyle = prev.gcs; };
  try {
    const m = mountTree(() => React.createElement(ImageStrip, { images: [video] }), (type, props) => (type === 'div' && props['data-chat-image'] === 'video' ? t.tile : {}));
    return { ...t, made, m, restore };
  } catch (e) { restore(); throw e; }
}

test('SABOTAGE lazy wiring: the mounted tile watches its own box against the thread\'s scroller (NEAR_PX margin) and loads only when near, once', () => {
  const { m, made, tile, scroller, restore } = mountTile();
  try {
    assert.equal(m.video.src, undefined, 'mounted off screen: no source');
    assert.equal(m.video.preload, 'none');
    assert.equal(m.tileNear, 'no');
    assert.equal(made.length, 1, 'the effect made one observer');
    assert.deepEqual(made[0].observed, [tile], 'it observes the tile\'s own box');
    assert.equal(made[0].opts.root, scroller, 'against the thread\'s scrolling container');
    assert.equal(made[0].opts.rootMargin, `${NEAR_PX}px`);
    made[0].cb([{ isIntersecting: false }]);
    m.flush();
    assert.equal(m.video.src, undefined, 'not near yet: still nothing to fetch');
    made[0].cb([{ isIntersecting: true }]);
    m.flush();
    assert.equal(m.video.src, `/api/media/${ID}`, 'near: the source is set');
    assert.equal(m.video.preload, 'metadata');
    assert.equal(m.tileNear, 'yes');
    assert.equal(m.video.muted, true, 'still muted');
    assert.equal(m.video.autoPlay, undefined, 'still never autoplay');
    assert.ok(made[0].disconnected >= 1, 'the observer is let go once it has loaded');
    assert.equal(made.length, 1, 'the re-render after `near` makes no second observer');
  } finally { restore(); }
});

test('SABOTAGE lazy wiring: unmounting the tile before it is near disconnects its observer, and a late callback changes nothing', () => {
  const { m, made, restore } = mountTile();
  try {
    assert.equal(made.length, 1);
    assert.equal(made[0].disconnected, 0);
    m.unmount();
    assert.equal(made[0].disconnected, 1, 'the effect\'s cleanup disconnected it');
    made[0].cb([{ isIntersecting: true }]);
    m.flush();
    assert.equal(m.video.src, undefined, 'nothing loads after unmount');
  } finally { restore(); }
});
