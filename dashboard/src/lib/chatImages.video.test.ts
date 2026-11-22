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
