/**
 * F1 client-side reducer test — the dashboard's stream state machine.
 *
 * The EventSource wiring is browser-only; the item-list reducer + fallback
 * decision are pure and tested here (imported straight from the dashboard lib).
 *
 * Run: cd api && npm run stream-test
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  applyStreamEvent,
  shouldFallback,
  MAX_CLIENT_ITEMS,
  type TranscriptStreamEvent,
} from '../../dashboard/src/lib/transcriptStream.js';

const item = (uuid: string, text: string): any => ({ kind: 'text', role: 'assistant', text, uuid });

const snapshot = (items: any[]): TranscriptStreamEvent =>
  ({ type: 'snapshot', id: 's:1', agent_id: 'a', session_id: 's', grammar_version: 2, items } as any);
const delta = (items: any[]): TranscriptStreamEvent =>
  ({ type: 'delta', id: 's:2', agent_id: 'a', session_id: 's', grammar_version: 2, items } as any);

test('snapshot replaces the item list', () => {
  const out = applyStreamEvent([item('u1', 'old')], snapshot([item('u2', 'new')]));
  assert.deepStrictEqual(out.map((i: any) => i.uuid), ['u2']);
});

test('delta appends to the item list', () => {
  const out = applyStreamEvent([item('u1', 'a')], delta([item('u2', 'b'), item('u3', 'c')]));
  assert.deepStrictEqual(out.map((i: any) => i.uuid), ['u1', 'u2', 'u3']);
});

test('item list is capped to MAX_CLIENT_ITEMS keeping the newest', () => {
  const many = Array.from({ length: MAX_CLIENT_ITEMS }, (_, i) => item(`u${i}`, 'x'));
  const out = applyStreamEvent(many, delta([item('u-new', 'tail')]));
  assert.equal(out.length, MAX_CLIENT_ITEMS);
  assert.equal((out[out.length - 1] as any).uuid, 'u-new');
  assert.equal((out[0] as any).uuid, 'u1'); // oldest dropped
});

test('fallback fires after 3 consecutive errors with no open, resets on open', () => {
  assert.equal(shouldFallback(0), false);
  assert.equal(shouldFallback(1), false);
  assert.equal(shouldFallback(2), false);
  assert.equal(shouldFallback(3), true);
  assert.equal(shouldFallback(7), true);
});
