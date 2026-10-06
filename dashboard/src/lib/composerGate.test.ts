import { test } from 'node:test';
import assert from 'node:assert/strict';
import { composerGate, delegatedWorkLabel } from './composerGate.ts';

// THE BUG (Shaw, 2026-10-06): a seat running a sub-agent read `working`, so chat refused to
// send — while its CLI was accepting and queueing the same message. Measured live: 17 seats
// had delegated work in flight; three were in exactly that state, one with the operator's
// message already queued on screen.

test('a WORKING seat can be sent to, and is told the message will be queued', () => {
  const g = composerGate({ state: 'working' });
  assert.equal(g.send, 'enabled', 'this is the reported bug: working must not block the send');
  assert.equal((g as { queued: boolean }).queued, true);
  assert.match((g as { reason: string }).reason, /queue/i);
});

test('`thinking` is the same case — the detector word must not slip the gate', () => {
  // The detector emits `thinking`; the web aliases it to `working`. All three live seats in
  // the bug report were `thinking`, so a gate written against `working` alone would miss
  // every real instance of it.
  const g = composerGate({ state: 'thinking' });
  assert.equal(g.send, 'enabled');
  assert.equal((g as { queued: boolean }).queued, true);
});

test('a running sub-agent NEVER gates the send, in any state', () => {
  // gm's ruling: subagents is informational. It must not become a second blocker.
  for (const state of ['working', 'idle', 'thinking', 'stalled']) {
    assert.equal(composerGate({ state, subagents: 3 }).send, 'enabled', state);
  }
});

test('a MENU on screen blocks the send — a keystroke there answers it', () => {
  // The protection actually worth keeping: the operator's message would be read as a choice
  // they never made.
  const g = composerGate({ state: 'idle', pendingMenu: { options: ['a', 'b'] } });
  assert.equal(g.send, 'blocked');
  assert.match((g as { reason: string }).reason, /question on screen|choice/i);
});

test('a menu blocks even when the seat is otherwise perfectly idle AND has subagents', () => {
  // Control: the menu rule must not be reachable only via some other blocked state.
  assert.equal(composerGate({ state: 'idle', subagents: 2, pendingMenu: { options: [] as string[], x: 1 } }).send, 'blocked');
});

test('a permission prompt blocks the send', () => {
  assert.equal(composerGate({ state: 'waiting' }).send, 'blocked');
});

test('a seat that is not running blocks the send', () => {
  for (const state of ['stopped', 'crashed', 'offline', 'retired']) {
    assert.equal(composerGate({ state }).send, 'blocked', state);
  }
});

test('an IDLE seat sends normally, with no queue warning', () => {
  // Positive control. Without this the gate could be "block everything" and still pass the
  // blocking tests above.
  const g = composerGate({ state: 'idle' });
  assert.equal(g.send, 'enabled');
  assert.equal((g as { queued: boolean }).queued, false);
});

test('an UNKNOWN state sends rather than blocks', () => {
  // Refusing on absent information is how a reachable agent becomes unreachable — the same
  // class of mistake as the bug itself.
  assert.equal(composerGate({ state: undefined }).send, 'enabled');
  assert.equal(composerGate({ state: 'something-new' }).send, 'enabled');
});

test('delegatedWorkLabel says nothing when there is nothing to say', () => {
  assert.equal(delegatedWorkLabel(0), null);
  assert.equal(delegatedWorkLabel(undefined), null);
  assert.equal(delegatedWorkLabel(1), '1 agent running');
  assert.equal(delegatedWorkLabel(3), '3 agents running');
});
