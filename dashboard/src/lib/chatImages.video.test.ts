/**
 * The inline video tile (components/kit/ChatImages.tsx VideoThumb, through ImageStrip): it is muted, has controls,
 * plays inline and NEVER autoplays (a thread full of recordings must not start playing, or make a sound, by itself).
 * It is lazy: until the tile is near the viewport (IntersectionObserver) it has no source and preload "none", so a
 * window of videos fetches nothing for the ones off screen; without IntersectionObserver it loads metadata at once.
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

test('SABOTAGE finding 5c: the inline video tile is muted and never autoplays', () => {
  const { html, props } = render();
  assertQuiet(props, html);
});

test('finding 6: the tile is lazy: no source and preload none until it is near the viewport', () => {
  const g = globalThis as { IntersectionObserver?: unknown };
  const observed: unknown[] = [];
  g.IntersectionObserver = class { constructor(_cb: unknown, opts: unknown) { observed.push(opts); } observe() {} disconnect() {} };
  try {
    const { html, props } = render();
    assert.equal(props.src, undefined, 'no source off screen');
    assert.equal(props.preload, 'none');
    assert.doesNotMatch(html, /src=/);
    assertQuiet(props, html);
  } finally { delete g.IntersectionObserver; }
  // No IntersectionObserver (an old browser): it loads its metadata at once, as before.
  const { props } = render();
  assert.equal(props.preload, 'metadata');
  assert.equal(props.src, `/api/media/${ID}`);
});
