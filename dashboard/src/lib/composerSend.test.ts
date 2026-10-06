import { test } from 'node:test';
import assert from 'node:assert/strict';
import { withTimeout, sendFailureNote, isSafeToRetry, SendTimeout, SEND_TIMEOUT_MS } from './composerSend.ts';

// A fake clock, so these are deterministic rather than sleeping for 15 real seconds.
function fakeTimers() {
  let next = 1;
  const armed = new Map<number, () => void>();
  const cleared: number[] = [];
  return {
    set: ((fn: () => void) => { const id = next++; armed.set(id, fn); return id; }) as unknown as typeof setTimeout,
    clear: ((id: number) => { cleared.push(id); armed.delete(id); }) as unknown as typeof clearTimeout,
    fire: () => { for (const fn of [...armed.values()]) fn(); },
    armedCount: () => armed.size,
    clearedCount: () => cleared.length,
  };
}

test('a resolved send resolves, and CLEARS its timer', async () => {
  // The leak: the old race left a timer armed for 15s after every successful send.
  const t = fakeTimers();
  const v = await withTimeout(Promise.resolve('ok'), 100, t.set, t.clear);
  assert.equal(v, 'ok');
  assert.equal(t.clearedCount(), 1, 'the timer was not cleared on the success path');
  assert.equal(t.armedCount(), 0);
});

test('a rejected send propagates its OWN error, and clears its timer', async () => {
  const t = fakeTimers();
  const boom = new Error('ECONNREFUSED');
  await assert.rejects(() => withTimeout(Promise.reject(boom), 100, t.set, t.clear), (e) => e === boom);
  assert.equal(t.clearedCount(), 1);
});

test('a hung send rejects with SendTimeout, not a bare Error', async () => {
  // The type is what lets the caller tell "we stopped waiting" from "it failed".
  const t = fakeTimers();
  const hung = new Promise<string>(() => {});
  const p = withTimeout(hung, 100, t.set, t.clear);
  t.fire();
  await assert.rejects(() => p, (e) => e instanceof SendTimeout);
});

test('a TIMEOUT must not be reported as "nothing was sent"', async () => {
  // THE BUG. The race settles our promise; the request is still in flight and the server
  // may deliver it. Telling the operator it failed is what makes them send again — and a
  // duplicate instruction is the one failure this surface exists to make legible.
  const note = sendFailureNote(new SendTimeout(SEND_TIMEOUT_MS));
  assert.doesNotMatch(note, /nothing was sent/i, 'a timeout claimed the send definitely failed');
  assert.match(note, /may/i, 'a timeout must report an UNKNOWN outcome');
  assert.match(note, /transcript/i, 'it must point the operator at how to check before resending');
});

test('a real connection failure DOES say nothing was sent', () => {
  // Positive control: the honest-failure copy must survive, or the fix above is just
  // deleting a message rather than distinguishing two cases.
  const note = sendFailureNote(new Error('ECONNREFUSED'));
  assert.match(note, /nothing was sent/i);
});

test('the two cases do not share copy', () => {
  assert.notEqual(sendFailureNote(new SendTimeout(1)), sendFailureNote(new Error('x')));
});

test('retry is offered after a refused connection and REFUSED after a timeout', () => {
  assert.equal(isSafeToRetry(new Error('ECONNREFUSED')), true);
  assert.equal(isSafeToRetry(new SendTimeout(1)), false);
});
