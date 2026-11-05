/**
 * The agent-tree stream state machine (lib/agentTreeStream.ts). The hook only wires EventSource and
 * timers to it: snapshot frames replace the tree, `end` and repeated errors fall back to polling
 * with backoff, a 404 (no Claude session: codex/gemini seats) means no tree and no more requests.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { initialTreeStream, reduceTreeStream, pollLane, POLL_MS, POLL_MAX_MS, type TreeStreamState } from './agentTreeStream';
import type { AgentTreeNode } from './agentTree';

const load = (name: string): AgentTreeNode =>
  JSON.parse(readFileSync(new URL(`./fixtures/agent-tree/${name}.json`, import.meta.url), 'utf8'));

test('a snapshot frame replaces the tree and resets errors', () => {
  let s = initialTreeStream();
  assert.equal(s.mode, 'connecting');
  assert.equal(s.tree, null);
  s = reduceTreeStream(s, { type: 'error' });
  s = reduceTreeStream(s, { type: 'snapshot', tree: load('one-running') });
  assert.equal(s.mode, 'stream');
  assert.equal(s.errors, 0);
  assert.equal(s.tree?.children.length, 1);
  s = reduceTreeStream(s, { type: 'snapshot', tree: load('depth3') });
  assert.equal(s.tree?.children.length, 4, 'replaced, not merged');
  s = reduceTreeStream(s, { type: 'snapshot', tree: null });
  assert.equal(s.tree, null, 'a null frame (no session) clears the tree');
  assert.equal(s.mode, 'stream');
});

test('end switches to polling at the base interval', () => {
  let s = reduceTreeStream(initialTreeStream(), { type: 'snapshot', tree: load('mixed') });
  s = reduceTreeStream(s, { type: 'end' });
  assert.equal(s.mode, 'polling');
  assert.equal(s.pollMs, POLL_MS);
  assert.equal(s.tree?.children.length, 3, 'the last tree stays on screen');
});

test('a 404 gives a null tree and stops (gone)', () => {
  let s = reduceTreeStream(initialTreeStream(), { type: 'snapshot', tree: load('mixed') });
  s = reduceTreeStream(s, { type: 'end' });
  s = reduceTreeStream(s, { type: 'poll', status: 404 });
  assert.equal(s.mode, 'gone');
  assert.equal(s.tree, null);
  assert.equal(reduceTreeStream(s, { type: 'snapshot', tree: load('mixed') }).mode, 'gone', 'gone is terminal');
});

test('three stream errors in a row fall back to polling; an open in between resets the count', () => {
  let s = initialTreeStream();
  s = reduceTreeStream(s, { type: 'error' });
  s = reduceTreeStream(s, { type: 'error' });
  assert.equal(s.mode, 'connecting');
  s = reduceTreeStream(s, { type: 'open' });
  assert.equal(s.errors, 0);
  s = reduceTreeStream(s, { type: 'error' });
  s = reduceTreeStream(s, { type: 'error' });
  assert.equal(s.mode, 'connecting');
  s = reduceTreeStream(s, { type: 'error' });
  assert.equal(s.mode, 'polling');
});

test('failed polls back off (doubling, capped); a good poll resets the interval', () => {
  let s: TreeStreamState = reduceTreeStream(initialTreeStream(), { type: 'end' });
  s = reduceTreeStream(s, { type: 'poll', status: 500 });
  assert.equal(s.pollMs, POLL_MS * 2);
  s = reduceTreeStream(s, { type: 'poll', status: 0 });
  assert.equal(s.pollMs, POLL_MS * 4);
  for (let i = 0; i < 10; i++) s = reduceTreeStream(s, { type: 'poll', status: 502 });
  assert.equal(s.pollMs, POLL_MAX_MS);
  s = reduceTreeStream(s, { type: 'poll', status: 200, tree: load('frozen-parent') });
  assert.equal(s.pollMs, POLL_MS);
  assert.equal(s.mode, 'polling');
  assert.equal(s.tree?.children.length, 1);
});

test('supportsAll: only when the API echoes all_finished on the root', () => {
  let s = reduceTreeStream(initialTreeStream(), { type: 'snapshot', tree: load('mixed') });
  assert.equal(s.supportsAll, false, 'an API that ignores ?all=1 hides the toggle');
  s = reduceTreeStream(s, { type: 'snapshot', tree: { ...load('mixed'), all_finished: false } });
  assert.equal(s.supportsAll, true);
  s = reduceTreeStream(s, { type: 'snapshot', tree: null });
  assert.equal(s.supportsAll, true, 'a null frame does not forget support');
});

test('every poll result bumps seq (the hook schedules the next poll on it); reset starts over', () => {
  let s = reduceTreeStream(initialTreeStream(), { type: 'end' });
  const a = s.seq;
  s = reduceTreeStream(s, { type: 'poll', status: 200, tree: load('mixed') });
  assert.equal(s.seq, a + 1);
  s = reduceTreeStream(s, { type: 'poll', status: 200, tree: load('mixed') });
  assert.equal(s.seq, a + 2, 'same mode and interval still schedules again');
  s = reduceTreeStream(s, { type: 'poll', status: 404 });
  s = reduceTreeStream(s, { type: 'reset' });
  assert.equal(s.mode, 'connecting', 'reset (a new seat or ?all) leaves gone');
  assert.equal(s.tree, null);
});

test('reset carries the request key, so a stale poll lane can tell it is not the current request', () => {
  let s = reduceTreeStream(initialTreeStream(), { type: 'reset', key: 'gm|0' });
  assert.equal(s.key, 'gm|0');
  s = reduceTreeStream(s, { type: 'poll', status: 404 });
  assert.equal(s.mode, 'gone');
  assert.equal(s.key, 'gm|0');
  s = reduceTreeStream(s, { type: 'reset', key: 'pm-x|0' });
  assert.equal(s.key, 'pm-x|0');
  assert.equal(s.mode, 'connecting');
  assert.equal(initialTreeStream().key, null);
});

test('pollLane: only this request\'s polling/gone state closes the stream; stale state from another seat never does', () => {
  const polling = reduceTreeStream(reduceTreeStream(initialTreeStream(), { type: 'reset', key: 'gm|0|1' }), { type: 'end' });
  const gone = reduceTreeStream(polling, { type: 'poll', status: 404 });
  assert.equal(pollLane(polling, 'gm|0|1', true), 'poll');
  assert.equal(pollLane(gone, 'gm|0|1', true), 'stop');
  assert.equal(pollLane(polling, 'pm-x|0|1', true), 'off', 'stale polling state, new seat: leave the new stream alone');
  assert.equal(pollLane(gone, 'pm-x|0|1', true), 'off', 'stale gone state, new seat');
  assert.equal(pollLane(polling, 'gm|1|1', true), 'off', 'toggling ?all is a new request too');
  assert.equal(pollLane(polling, 'gm|0|1', false), 'off');
  const streaming = reduceTreeStream(reduceTreeStream(initialTreeStream(), { type: 'reset', key: 'gm|0|1' }), { type: 'snapshot', tree: null });
  assert.equal(pollLane(streaming, 'gm|0|1', true), 'off');
});
