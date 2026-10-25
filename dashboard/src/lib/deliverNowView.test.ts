import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bannerView } from './deliverNowView.ts';

const NOW = 1_800_000_000;

test('nothing waiting, no banner', () => {
  assert.equal(bannerView(undefined, NOW), null);
  assert.equal(bannerView({ count: 0 }, NOW), null);
});

test('waiting mail says how many and why, with Deliver now', () => {
  const v = bannerView({ count: 2, reason: 'it was active a moment ago' }, NOW)!;
  assert.equal(v.text, '2 messages waiting: it was active a moment ago');
  assert.equal(v.canDeliver, true);
  assert.equal(bannerView({ count: 1 }, NOW)!.text, '1 message waiting');
});

test('after Deliver now, the button goes and the remaining reason shows', () => {
  const v = bannerView({ count: 1, reason: 'it is busy (mid-turn, a prompt on screen, or text typed in its box)',
                         waiver: { by: 'web', expires_at: NOW + 60 } }, NOW)!;
  assert.equal(v.canDeliver, false);
  assert.match(v.text, /delivering as soon as it can: it is busy/);
  assert.equal(bannerView({ count: 1, waiver: { expires_at: NOW - 1 } }, NOW)!.canDeliver, true);  // expired
});
