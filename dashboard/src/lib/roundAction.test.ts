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

test('an empty box always shows the call, never a Send (Shaw, 2026-11-19): a call that cannot start is a dimmed Call with the reason', () => {
  const v = roundView({ hasContent: false, call: call({ available: false, reason: "Voice isn't set up for Sample yet" }) }, { agent: 'Sample' });
  assert.deepEqual([v.mode, v.enabled, v.label, v.title], ['call', false, 'Call Sample', "Voice isn't set up for Sample yet"]);
  const noReason = roundView({ hasContent: false, call: call({ available: false }) }, { agent: 'Sample' });
  assert.deepEqual([noReason.mode, noReason.enabled], ['call', false]);
  assert.match(noReason.title, /Sample/);
  const none = roundView({ hasContent: false });
  assert.deepEqual([none.mode, none.enabled], ['idle', false], 'a composer with no call at all keeps its disabled Send');
  assert.equal(roundView({ hasContent: true, sending: true }).enabled, false);
  assert.equal(roundView({ hasContent: true, blocked: true }).enabled, false);
});

test('the three states of the box: empty -> Call, text (or an attachment) -> Send, cleared -> Call again', () => {
  for (const available of [true, false]) {
    const c = call({ available });
    assert.equal(roundView({ hasContent: false, call: c }).mode, 'call');
    assert.equal(roundView({ hasContent: true, call: c }).mode, 'send', 'text or an attachment is a Send');
    assert.equal(roundView({ hasContent: false, call: c }).mode, 'call', 'clearing the box brings the call back');
  }
});
