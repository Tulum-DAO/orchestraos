import { test } from 'node:test';
import assert from 'node:assert/strict';
import { refreshHealthUntilReady } from './handsFreeHealth.ts';

const ok = { ok: true, live: true } as const;
const down = { ok: false, error: 'HTTP 502' } as const;

test('a failed or not-ready read is retried until a call is possible, and every read is reported', async () => {
  const seen: unknown[] = [];
  const reads = [down, { ok: true, live: false }, ok];
  let n = 0;
  const last = await refreshHealthUntilReady(async () => reads[n++], (h) => seen.push(h), { delays: [0, 0, 0], sleep: async () => {} });
  assert.equal(n, 3);
  assert.deepEqual(seen, reads);
  assert.equal(last, ok);
});

test('stops as soon as a call is possible: one read when the first is ready', async () => {
  let n = 0;
  await refreshHealthUntilReady(async () => { n++; return ok; }, () => {}, { delays: [0, 0], sleep: async () => {} });
  assert.equal(n, 1);
});

test('gives up after its retries, and a cancelled refresh reports nothing more', async () => {
  let n = 0;
  await refreshHealthUntilReady(async () => { n++; return down; }, () => {}, { delays: [0, 0], sleep: async () => {} });
  assert.equal(n, 3, 'the first read plus one per delay');
  let cancelled = false; const seen: unknown[] = [];
  await refreshHealthUntilReady(async () => { cancelled = true; return down; }, (h) => seen.push(h),
    { delays: [0, 0], sleep: async () => {}, cancelled: () => cancelled });
  assert.deepEqual(seen, [], 'a pane closed mid-read never sets state');
});

test('a disabled Arturo is final: no retries', async () => {
  let n = 0;
  await refreshHealthUntilReady(async () => { n++; return { ok: false, disabled: true }; }, () => {}, { delays: [0, 0], sleep: async () => {} });
  assert.equal(n, 1);
});

test('the dimmed call says why: checking, not answering, or the key the server reported missing', async () => {
  const { callUnavailableReason } = await import('./handsFreeHealth.ts');
  assert.match(callUnavailableReason(null, 'needs a key', 'Arturo'), /Checking/);
  assert.match(callUnavailableReason({ ok: false, error: 'HTTP 502' }, 'needs a key', 'Arturo'), /Arturo isn't answering/);
  assert.equal(callUnavailableReason({ ok: true, live: false }, 'needs a key', 'Arturo'), 'needs a key');
});

test('cancelled during a sleep: no further read', async () => {
  let n = 0; let cancelled = false;
  await refreshHealthUntilReady(async () => { n++; return down; }, () => {},
    { delays: [0, 0], sleep: async () => { cancelled = true; } , cancelled: () => cancelled });
  assert.equal(n, 1);
});
