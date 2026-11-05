// The agent chat never scrolls sideways on a mouse screen (2026-11-04). Every bubble carries its hover icons (.rx-bar,
// components/kit/ReactionBadges.tsx SlotBar): invisible until hovered, but laid out. Under a bubble narrower than the bar,
// `left: 0` (user) / `right: 0` (agent) hung it past the bubble's far edge and, near the scroller's edge, past the
// scroller's, so the chat grew a horizontal scroll. The bar now never reaches past the bubble's far edge, and the chat's
// scroller clips x as a safety net (components/chat/TranscriptChatView.tsx).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { BAR_OFFSET, BAR_WIDTH } from './reactionLayout.ts';

const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');
const css = src('components/kit/reactions.css');
const rule = (sel: string) => {
  const i = css.indexOf(`${sel} {`);
  assert.ok(i >= 0, `rule ${sel} exists`);
  return css.slice(i, css.indexOf('}', i)).replace(/\s+/g, ' ');
};

test('the bar width and outside offset are CSS variables equal to BAR_WIDTH / BAR_OFFSET (lib/reactionLayout)', () => {
  assert.match(rule('.rx-bar'), new RegExp(`--rx-bar-w: ${BAR_WIDTH}px;`));
  assert.match(rule('.rx-bar'), new RegExp(`--rx-bar-off: ${BAR_OFFSET}px;`));
  assert.match(rule('.rx-bar[data-place="out"][data-side="agent"]'), /left: calc\(100% \+ var\(--rx-bar-off\)\);/);
  assert.match(rule('.rx-bar[data-place="out"][data-side="user"]'), /right: calc\(100% \+ var\(--rx-bar-off\)\);/);
});

test('user side, under: a bubble narrower than the bar gets the bar right-aligned to the bubble', () => {
  const r = rule('.rx-bar[data-place="under"][data-side="user"]');
  assert.match(r, /left: min\(0px, calc\(100% - var\(--rx-bar-w\)\)\);/);
  assert.doesNotMatch(r, /left: 0;/);
});

test('agent side, under: mirrored, the bar never hangs past the bubble\'s left edge', () => {
  const r = rule('.rx-bar[data-place="under"][data-side="agent"]');
  assert.match(r, /right: min\(0px, calc\(100% - var\(--rx-bar-w\)\)\);/);
  assert.doesNotMatch(r, /right: 0;/);
});

test('the chat scroller clips x in both the full and the compact layout', () => {
  const tsx = src('components/chat/TranscriptChatView.tsx');
  const i = tsx.indexOf('data-testid="transcript-scroll"');
  assert.ok(i >= 0);
  const cls = tsx.slice(i, tsx.indexOf('>', tsx.indexOf('}', tsx.indexOf('className=', i)) ));
  const branches = [...cls.matchAll(/'([^']*overflow-y-auto[^']*)'/g)].map((m) => m[1]);
  assert.equal(branches.length, 2, 'compact and full scroller classes');
  for (const b of branches) assert.match(b, /(^|\s)overflow-x-hidden(\s|$)/, b);
});
