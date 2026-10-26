/**
 * The chat fold's tags (ios-watch-dev msg_1491bb2c): what is drawn in the conversation (user)
 * vs folded into the strip (agent), and which operator message each item belongs to (exchange).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { tagAudience, tagSynthetics, openerKind } from './audience.js';

let n = 0;
const u = (text: string, extra: any = {}) => ({ kind: 'text', role: 'user', text, uuid: `u${++n}`, ...extra });
const a = (text: string) => ({ kind: 'text', role: 'assistant', text, uuid: `a${++n}` });
const tool = () => ({ kind: 'tool_use', role: 'assistant', tool: 'Bash', input: {}, summary: 'ls', id: `t${++n}`, uuid: `t${n}` });
const res = () => ({ kind: 'tool_result', role: 'user', text: 'ok', uuid: `r${++n}` });
const think = () => ({ kind: 'thinking', role: 'assistant', text: 'hmm', uuid: `k${++n}` });
const tags = (xs: any[]) => xs.map((x) => `${x.uuid}:${x.audience}${x.exchange ? '@' + x.exchange : ''}`);

test('an operator turn: his message, the FIRST reply and the closing reply are drawn, the rest folds', () => {
  // Shaw, 2026-10-22 03:58 ET: "I would expect to see the first response at least to my message that I
  // sent ... and then the div, which is collapsible. And then the most recent message."
  const m = u('fix the card');
  const xs = [m, think(), a('looking'), tool(), res(), a('still on it'), tool(), res(), a('done: the card answers now')];
  tagAudience(xs);
  assert.deepEqual(xs.map((x) => x.audience),
    ['user', 'agent', 'user', 'agent', 'agent', 'agent', 'agent', 'agent', 'user']);
  assert.ok(xs.every((x) => x.exchange === m.uuid));
});

test('a turn mail or a hook opened is the strip, its reply included', () => {
  for (const opener of ['[MSG from gm | high] (sent 1m ago) do X', 'Stop hook feedback:\n[QUEUE-DIGEST] 1 pending',
                        '[APPROVAL RESOLVED apr_1] answered', '[WAL DELTA] hydrate']) {
    const xs = [u('earlier from the operator'), a('ok'), u(opener), tool(), res(), a('handled the mail')];
    tagAudience(xs);
    assert.deepEqual(xs.slice(2).map((x) => x.audience), ['agent', 'agent', 'agent', 'agent'], opener);
    assert.equal(xs[1].audience, 'user');
    assert.ok(xs.slice(2).every((x) => x.exchange === xs[0].uuid), 'it stays inside his exchange');
  }
});

test('a job he set off reports back: the notification folds, its closing reply is drawn', () => {
  const xs = [u('<task-notification>\n<task-id>b1</task-id> completed</task-notification>'), a('reading it'), a('the build passed')];
  tagAudience(xs);
  assert.deepEqual(xs.map((x) => x.audience), ['agent', 'agent', 'user']);
  assert.equal(openerKind(xs[0]), 'notification');
});

test('the spawn brief and anything before the first opener fold (a log with no operator turn draws only its newest reply)', () => {
  const xs = [a('stray'), u('You are gm. Read your init', { is_system: true }), a('ready')];
  tagAudience(xs);
  // 'ready' is drawn by the iOS 334 rule (no recorded turns: the most recent message is shown).
  assert.deepEqual(xs.map((x) => x.audience), ['agent', 'agent', 'user']);
  assert.ok(xs.every((x) => x.exchange === undefined));
});

test('a message he sends mid-turn opens its own exchange', () => {
  const first = u('start the build');
  const second = u('also check the logs', { queued: true });
  const xs = [first, tool(), res(), second, a('build started, logs clean')];
  tagAudience(xs);
  assert.deepEqual(tags(xs).map((t) => t.split(':')[1]), [`user@${first.uuid}`, `agent@${first.uuid}`, `agent@${first.uuid}`,
                                                          `user@${second.uuid}`, `user@${second.uuid}`]);
});

test('slash commands and their output are dev rows, never his speech', () => {
  const xs = [u('<command-name>/model</command-name>'), u('<local-command-stdout>Set model to Opus 5.5</local-command-stdout>')];
  tagAudience(xs);
  assert.deepEqual(xs.map((x) => x.audience), ['agent', 'agent']);
});

test('synthetic rows after the merge: a pending message is his, agent mail folds in the current exchange', () => {
  const xs: any[] = [u('hello'), a('hi')];
  tagAudience(xs);
  xs.push({ kind: 'queued_batch', entries: [], key: 'msg:1' }, u('still waiting', { uuid: 'queued:9', queued: true }));
  tagSynthetics(xs);
  assert.equal(xs[2].audience, 'agent');
  assert.equal(xs[2].exchange, xs[0].uuid);
  assert.equal(xs[3].audience, 'user');
  assert.equal(xs[3].exchange, 'queued:9');
});


test('one reply in his turn is drawn once (it is both the first and the last)', () => {
  const xs = [u('status?'), think(), a('all green')];
  tagAudience(xs);
  assert.deepEqual(xs.map((x) => x.audience), ['user', 'agent', 'user']);
});

test('a job he set off still shows only its closing reply (not its first)', () => {
  const xs = [u('<task-notification>\n<task-id>b2</task-id> completed</task-notification>'), a('reading it'), tool(), res(), a('the build passed')];
  tagAudience(xs);
  assert.deepEqual(xs.map((x) => x.audience), ['agent', 'agent', 'agent', 'agent', 'user']);
});

test('a hook the route hides never turns the reply he waited for into a step (ios-watch-dev hidden-hook case)', () => {
  // The stop-hook digest is isMeta and dropped by the route, so his turn simply runs on: the
  // first reply after his message is still his first reply, the latest still the closing one.
  const m = u('ship it');
  const xs = [m, a('shipping now'), tool(), res(), a('handled the queue digest'), a('shipped: #467 is merged')];
  tagAudience(xs);
  assert.deepEqual(xs.map((x) => x.audience), ['user', 'user', 'agent', 'agent', 'agent', 'user']);
  assert.ok(xs.every((x) => x.exchange === m.uuid));
});

test('a log with no turn the operator opened still draws its NEWEST reply (iOS 334)', () => {
  // "if there are no recorded turns, display the most recent message": only mail in the window,
  // and the newest assistant text is drawn; everything else still folds, with no exchange.
  const xs = [u('[MSG from gm | high] do X'), a('on it'), tool(), res(), a('X done'),
              u('Stop hook feedback:\n[QUEUE-DIGEST] 1 pending'), think(), a('acked, idle')];
  tagAudience(xs);
  assert.deepEqual(xs.map((x) => x.audience), ['agent', 'agent', 'agent', 'agent', 'agent', 'agent', 'agent', 'user']);
  assert.ok(xs.every((x) => x.exchange === undefined));
  const none = [u('[MSG from gm | low] fyi'), tool(), res()];                 // no reply at all: nothing to draw
  tagAudience(none);
  assert.ok(none.every((x) => x.audience === 'agent'));
});

test('a log WITH an operator turn keeps the fold: a later reply to mail stays folded', () => {
  const m = u('fix the card');
  const xs = [m, a('done'), u('[MSG from gm | low] fyi'), a('noted')];
  tagAudience(xs);
  assert.deepEqual(xs.map((x) => x.audience), ['user', 'user', 'agent', 'agent']);
});

test('an operator turn whose message has no uuid still counts: the no-turn rule never fires', () => {
  const m = { kind: 'text', role: 'user', text: 'fix the card' };              // no uuid on his message
  const xs: any[] = [m, a('done'), u('[MSG from gm | low] fyi'), a('noted')];
  tagAudience(xs);
  assert.deepEqual(xs.map((x) => x.audience), ['user', 'user', 'agent', 'agent']);
});
