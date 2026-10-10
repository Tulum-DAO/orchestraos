import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  feedHealthOf, staleStyleFor, isDegraded, lastSeenLabel, STALE_AFTER_MS, DISCONNECTED_AFTER_MS,
  updatingStyleFor, styleForAgentState, FETCH_OUTSTANDING_MS,
} from './feedLiveness.ts';

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

test('never-fetched with nothing in flight is disconnected, and is not confused with stale', () => {
  assert.deepEqual(feedHealthOf({ hasData: false, now: NOW }), { health: 'disconnected' });
  assert.deepEqual(feedHealthOf({ hasData: true, dataUpdatedAt: 0, now: NOW }), { health: 'disconnected' });
  assert.equal(feedHealthOf({ hasData: false, now: NOW }).lastSeenAt, undefined);
  assert.equal(staleStyleFor(feedHealthOf({ hasData: false, now: NOW }))?.label, 'disconnected');
});

test('THE COLD LOAD: a first fetch in flight is CONNECTING, not an outage', () => {
  // The defect this replaces: feedHealthOf returned `disconnected` for ANY !hasData, so the
  // rail's `isLoading && !data && health !== 'disconnected'` guard contradicted itself and
  // could never be true. Every cold page load fell through to the banner and told the operator
  // "Not connected to the fleet" while the fleet was healthy and the first fetch was in flight.
  assert.equal(feedHealthOf({ hasData: false, isFetching: true, now: NOW }).health, 'connecting');
  // And it paints NOTHING: no grey override, no alarm.
  assert.equal(staleStyleFor(feedHealthOf({ hasData: false, isFetching: true, now: NOW })), undefined);
  assert.equal(isDegraded(feedHealthOf({ hasData: false, isFetching: true, now: NOW })), false);
});

test('CONNECTING cannot become the eternal spinner it replaced', () => {
  // The branch it replaces existed to stop "Loading…" showing forever against a stopped API.
  // Connecting is therefore conditioned on a fetch being in flight AND not having failed, so
  // every way of ceasing to be in-flight-and-healthy lands back on disconnected.
  assert.equal(feedHealthOf({ hasData: false, isFetching: false, now: NOW }).health, 'disconnected');
  assert.equal(feedHealthOf({ hasData: false, isFetching: true, isError: true, now: NOW }).health, 'disconnected');
  // The CONTROL: disconnected is still degraded, so the alarm still fires when it is real.
  assert.equal(isDegraded(feedHealthOf({ hasData: false, isFetching: false, now: NOW })), true);
  assert.equal(staleStyleFor(feedHealthOf({ hasData: false, now: NOW }))?.label, 'disconnected');
});

test('isDegraded is the warn predicate, and it is NOT `!== live`', () => {
  // `health !== 'live'` is what silently swept connecting in. Every surface asks isDegraded.
  assert.equal(isDegraded({ health: 'live' }), false);
  assert.equal(isDegraded({ health: 'connecting' }), false);
  assert.equal(isDegraded({ health: 'stale' }), true);
  assert.equal(isDegraded({ health: 'disconnected' }), true);
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

// ---- THE HIDDEN TAB (Shaw 2026-10-10: every status blank for ~2 min on returning to the tab) ----
// The browser pauses the poll while the tab is hidden. Coming back after 10 minutes, the last
// success is 10 minutes old through no fault of the feed: that must read UPDATING (dimmed, last
// known colours), then LIVE within one fetch, with no grey frame in between.


const TEN_MIN = 10 * 60_000;
const WORKING = { label: 'working', dot: 'bg-orange-500 animate-pulse', text: 'text-orange-300' };

test('hidden 10 min -> visible: every frame until the refresh lands is UPDATING, never grey', () => {
  const back = NOW;                       // the tab became visible now
  const last = NOW - TEN_MIN;             // last success before it was hidden
  const frames = [
    // the instant it is visible, before the refetch has even started
    feedHealthOf({ dataUpdatedAt: last, hasData: true, visibleSince: back, now: back }),
    // the refetch in flight, 1 s and 9 s in
    feedHealthOf({ dataUpdatedAt: last, hasData: true, isFetching: true, fetchStartedAt: back, visibleSince: back, now: back + 1_000 }),
    feedHealthOf({ dataUpdatedAt: last, hasData: true, isFetching: true, fetchStartedAt: back, visibleSince: back, now: back + 9_000 }),
  ];
  for (const f of frames) {
    assert.equal(f.health, 'updating');
    assert.equal(isDegraded(f), false);                       // no "Connection lost" banner
    assert.equal(staleStyleFor(f), undefined);                // no grey
  }
  // the fetch lands: live
  assert.equal(feedHealthOf({ dataUpdatedAt: back + 1_200, hasData: true, visibleSince: back, now: back + 1_300 }).health, 'live');
});

test('WHILE hidden the verdict is frozen at the moment it was hidden: no grey painted in the background', () => {
  // orchestra-builder's probe (hide 130 s): the throttled ticker kept re-verdicting the HIDDEN tab, the
  // data aged past DISCONNECTED_AFTER_MS, the tab painted grey "disconnected" in the background, and that
  // frame was the first thing the operator saw on return. The poll is paused while hidden, so age that
  // accrues then says nothing about the feed.
  const hid = NOW;
  const last = hid - 2_000;               // a fresh success just before the tab was hidden
  for (const t of [1_000, STALE_AFTER_MS, DISCONNECTED_AFTER_MS, TEN_MIN]) {
    const v = feedHealthOf({ dataUpdatedAt: last, hasData: true, hiddenSince: hid, now: hid + t });
    assert.equal(v.health, 'live', `hidden ${t} ms`);
    assert.equal(staleStyleFor(v), undefined);
  }
  // and the first verdict after it becomes visible is UPDATING (dimmed), never grey
  const back = hid + TEN_MIN;
  const first = feedHealthOf({ dataUpdatedAt: last, hasData: true, visibleSince: back, now: back });
  assert.equal(first.health, 'updating');
  // a verdict that was already degraded when the tab was hidden stays degraded: hiding is not a cure
  assert.ok(isDegraded(feedHealthOf({ dataUpdatedAt: hid - 45_000, hasData: true, hiddenSince: hid, now: hid + TEN_MIN })));
  // an ERROR while hidden still greys
  assert.ok(isDegraded(feedHealthOf({ dataUpdatedAt: last, hasData: true, isError: true, hiddenSince: hid, now: hid + TEN_MIN })));
});

test('UPDATING paints the last-known colour DIMMED, without the pulse, and says so', () => {
  const v = feedHealthOf({ dataUpdatedAt: NOW - TEN_MIN, hasData: true, isFetching: true, fetchStartedAt: NOW, visibleSince: NOW, now: NOW });
  const s = styleForAgentState(v, WORKING);
  assert.match(s.dot, /bg-orange-500/);
  assert.match(s.dot, /opacity-40/);
  assert.doesNotMatch(s.dot, /animate-pulse/);
  assert.match(s.label, /status updating…$/);
  assert.notEqual(s.dot, 'bg-neutral-600');
  // control: live is untouched, and updatingStyleFor says nothing about other states
  assert.deepEqual(styleForAgentState(feedHealthOf({ dataUpdatedAt: NOW, hasData: true, now: NOW }), WORKING), WORKING);
  assert.equal(updatingStyleFor({ health: 'live' }, WORKING), undefined);
});

test('a FAILING fetch still greys, hidden tab or not', () => {
  // errored after coming back
  const err = feedHealthOf({ dataUpdatedAt: NOW - TEN_MIN, hasData: true, isError: true, visibleSince: NOW - 2_000, now: NOW });
  assert.ok(isDegraded(err));
  assert.equal(staleStyleFor(err)?.dot, 'bg-neutral-600');
  // errored on fresh data: stale, not disconnected
  assert.equal(feedHealthOf({ dataUpdatedAt: NOW - 5_000, hasData: true, isError: true, now: NOW }).health, 'stale');
});

test('a fetch outstanding longer than FETCH_OUTSTANDING_MS is stale, not "updating" forever', () => {
  const at = (inFlightMs: number) => feedHealthOf({
    dataUpdatedAt: NOW - TEN_MIN, hasData: true, isFetching: true,
    fetchStartedAt: NOW - inFlightMs, visibleSince: NOW - inFlightMs, now: NOW,
  });
  assert.equal(at(FETCH_OUTSTANDING_MS).health, 'updating');          // boundary: still waiting
  assert.ok(isDegraded(at(FETCH_OUTSTANDING_MS + 1)));                // past it: not answering
  // even over FRESH data, a hung fetch is not live
  assert.ok(isDegraded(feedHealthOf({ dataUpdatedAt: NOW - 2_000, hasData: true, isFetching: true, fetchStartedAt: NOW - 11_000, now: NOW })));
});

test('visible all along and no success for a stale window: still the P1 (stale), not updating', () => {
  // the poller went silent while the operator was LOOKING (visibleSince long ago / unknown)
  assert.equal(feedHealthOf({ dataUpdatedAt: NOW - 45_000, hasData: true, visibleSince: NOW - TEN_MIN, now: NOW }).health, 'stale');
  assert.equal(feedHealthOf({ dataUpdatedAt: NOW - 45_000, hasData: true, now: NOW }).health, 'stale');
  // but back from hidden 5 s ago, the same data is just waiting on the refetch
  assert.equal(feedHealthOf({ dataUpdatedAt: NOW - 45_000, hasData: true, visibleSince: NOW - 5_000, now: NOW }).health, 'updating');
});

test('ONE agents poller: no view defines its own (slower) ["agents"] query', async () => {
  // gm 2026-10-10: a view that renders dots must not sit on a slower observer. Tasks ran its own
  // 30 s one (with its own copy of the fetcher), Analytics a 15 s one. Everything goes through
  // hooks/useAgents.ts (3 s) now; a new useQuery(['agents']) anywhere else fails here.
  const { readdirSync, readFileSync, statSync } = await import('node:fs');
  const { join, relative } = await import('node:path');
  const root = new URL('..', import.meta.url).pathname;
  const own: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) { walk(p); continue; }
      if (!/\.(ts|tsx)$/.test(name) || /\.test\./.test(name)) continue;
      const rel = relative(root, p);
      if (rel === 'hooks/useAgents.ts') continue;
      const src = readFileSync(p, 'utf8');
      // invalidate/refetch by key is fine; DEFINING a query with that key is not
      if (/useQuery\(\s*\{[^}]*queryKey:\s*\[\s*'agents'\s*\]/s.test(src)) own.push(rel);
    }
  };
  walk(root);
  assert.deepEqual(own, []);
  assert.match(readFileSync(join(root, 'hooks/useAgents.ts'), 'utf8'), /refetchInterval:\s*3_000/);
});
