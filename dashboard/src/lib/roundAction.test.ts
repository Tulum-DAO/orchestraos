import { test } from 'node:test';
import assert from 'node:assert/strict';
import { roundView } from './roundAction.ts';

const call = (over: Partial<{ inCall: boolean; available: boolean; reason: string }> = {}) => ({ inCall: false, available: true, ...over });

test('one round button: Send with something to send, Call when empty, End during a call', () => {
  assert.equal(roundView({ hasContent: true, call: call() }).mode, 'send');
  assert.equal(roundView({ hasContent: false, call: call() }, { agent: 'Sample' }).label, 'Call Sample');
  assert.equal(roundView({ hasContent: false, call: call({ inCall: true }) }).mode, 'end');
  assert.equal(roundView({ hasContent: true, call: call({ inCall: true }) }).mode, 'end', 'a live call keeps its End');
});

test('mid-turn with nothing to send: Stop; text typed mid-turn is still a Send (the CLI queues it)', () => {
  assert.equal(roundView({ hasContent: false, canStop: true, call: call() }).mode, 'stop');
  assert.equal(roundView({ hasContent: true, canStop: true, call: call() }).mode, 'send');
});

test('no call possible: never a dead Call, a disabled Send with the reason', () => {
  const v = roundView({ hasContent: false, call: call({ available: false, reason: "Voice isn't set up for Sample yet" }) });
  assert.deepEqual([v.mode, v.enabled, v.label, v.title], ['idle', false, 'Send', "Voice isn't set up for Sample yet"]);
  const none = roundView({ hasContent: false });
  assert.deepEqual([none.mode, none.enabled], ['idle', false]);
  assert.equal(roundView({ hasContent: true, sending: true }).enabled, false);
  assert.equal(roundView({ hasContent: true, blocked: true }).enabled, false);
});
