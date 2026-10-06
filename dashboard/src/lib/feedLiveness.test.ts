import { test } from 'node:test';
import assert from 'node:assert/strict';
import { feedHealthOf, staleStyleFor, lastSeenLabel, STALE_AFTER_MS, DISCONNECTED_AFTER_MS } from './feedLiveness.ts';

const NOW = 1_700_000_000_000;
const at = (ageMs: number, extra = {}) =>
  feedHealthOf({ dataUpdatedAt: NOW - ageMs, hasData: true, now: NOW, ...extra });

test('fresh data is live', () => {
  assert.equal(at(0).health, 'live');
  assert.equal(at(9_000).health, 'live');
  assert.equal(at(STALE_AFTER_MS - 1).health, 'live');
});

test('THE P1: data that stopped arriving is STALE, never live', () => {
  // The hour-long outage: rows from 15:33 still on screen at 16:30.
  assert.equal(at(STALE_AFTER_MS).health, 'stale');
  assert.equal(at(60_000).health, 'stale');
  assert.equal(at(DISCONNECTED_AFTER_MS - 1).health, 'stale');
  assert.equal(at(DISCONNECTED_AFTER_MS).health, 'disconnected');
  assert.equal(at(3_600_000).health, 'disconnected');
});

test('an ERROR never reads live, even over seconds-old data', () => {
  // "live" is a claim about NOW. A failing fetch means the next answer is unknown.
  assert.equal(at(0, { isError: true }).health, 'stale');
  assert.equal(at(1_000, { isError: true }).health, 'stale');
  // but one dropped poll over fresh data must not flap the whole UI to DISCONNECTED
  assert.notEqual(at(1_000, { isError: true }).health, 'disconnected');
});

test('never-fetched is disconnected, and is not confused with stale', () => {
  assert.deepEqual(feedHealthOf({ hasData: false, now: NOW }), { health: 'disconnected' });
  assert.deepEqual(feedHealthOf({ hasData: true, dataUpdatedAt: 0, now: NOW }), { health: 'disconnected' });
  assert.equal(feedHealthOf({ hasData: false, now: NOW }).lastSeenAt, undefined);
  assert.equal(staleStyleFor(feedHealthOf({ hasData: false, now: NOW }))?.label, 'disconnected');
});

test('a NOT-LIVE surface never keeps the cached colour or the pulse', () => {
  // The pulse is what made a dead feed read as an agent mid-turn.
  for (const ageMs of [STALE_AFTER_MS, 60_000, DISCONNECTED_AFTER_MS, 3_600_000]) {
    const style = staleStyleFor(at(ageMs));
    assert.ok(style, `age ${ageMs} produced no stale style`);
    assert.equal(style!.dot, 'bg-neutral-600');
    assert.ok(!/animate-pulse|amber|orange|blue|green/.test(style!.dot), `stale dot kept a live colour: ${style!.dot}`);
    assert.match(style!.label, /last seen \d\d:\d\d/);
  }
});

test('a LIVE feed gets no override, so the real state still shows', () => {
  assert.equal(staleStyleFor(at(0)), undefined);
  assert.equal(staleStyleFor(at(5_000)), undefined);
});

test('the label names a clock time, and says so when there is none', () => {
  assert.equal(lastSeenLabel(undefined), 'never connected');
  assert.match(lastSeenLabel(NOW), /^last seen \d\d:\d\d$/);
});

test('age is reported for a stale verdict so a caller can say how old', () => {
  assert.equal(at(45_000).ageMs, 45_000);
  assert.equal(at(45_000).lastSeenAt, NOW - 45_000);
  // clock skew (a server ahead of the browser) must not produce a negative age
  assert.equal(feedHealthOf({ dataUpdatedAt: NOW + 5_000, hasData: true, now: NOW }).ageMs, 0);
});
