import { test } from 'node:test';
import assert from 'node:assert/strict';
import { holdSendsUntil, waitForChatCli } from './devSwitchHold.ts';

test('a send after Back to chat waits for the switch answer (the CLI booted), then goes', async () => {
  let answer!: (v: unknown) => void;
  holdSendsUntil('dev-demo', new Promise((r) => { answer = r; }));
  let sent = false;
  const send = waitForChatCli('dev-demo', 3000).then(() => { sent = true; });
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(sent, false, 'held while the switch is pending');
  answer({ status: 200, body: { ok: true, booted: true, settled: true } });
  await send;
  assert.equal(sent, true);
  // Answered: the next send does not wait at all.
  const t0 = Date.now();
  await waitForChatCli('dev-demo', 3000);
  assert.ok(Date.now() - t0 < 20);
});

test('the hold is at most its limit, a failed switch does not block, and other seats never wait', async () => {
  holdSendsUntil('dev-demo', new Promise(() => {}));
  const t0 = Date.now();
  await waitForChatCli('dev-demo', 60);
  const waited = Date.now() - t0;
  assert.ok(waited >= 55 && waited < 400, `waited ${waited} ms`);
  const t1 = Date.now();
  await waitForChatCli('qa-demo', 3000);
  assert.ok(Date.now() - t1 < 20);
  holdSendsUntil('pm-demo', Promise.reject(new Error('no answer'))).catch(() => {});
  await waitForChatCli('pm-demo', 3000);
});
