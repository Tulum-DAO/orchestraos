/**
 * The stream tailer is shared per seat, but every subscriber has ITS OWN window: a snapshot holds the newest `limit`
 * items of the subscriber that receives it (avatar-dev r39: a detail panel opened while the chat streamed got the first
 * subscriber's 40-item snapshots), and no subscriber is ever sent more items at once than its limit: a resume or a
 * growth that would carry more arrives as that subscriber's snapshot (after an API restart the first delta drew up to
 * 1500 rows).
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/services/transcript-tail.window.test.ts (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync, appendFileSync } from 'node:fs';
import { join } from 'node:path';

const dir = testTmpDir('tail-window-');
process.env.ORCHESTRA_DIR = join(dir, 'orch');
process.env.HOME = join(dir, 'home');
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
const { TranscriptTailer } = await import('./transcript-tail.js');
type TailEvent = import('./transcript-tail.js').TailEvent;

let seq = 0;
const T = (n: number) => new Date(Date.UTC(2026, 10, 26, 8, 0, n % 60, Math.floor(n / 60))).toISOString();
const line = (text: string, role: 'user' | 'assistant' = 'assistant') => JSON.stringify({
  type: role, uuid: `${role[0]}-${++seq}`, timestamp: T(seq),
  message: role === 'user' ? { role, content: text } : { id: `m-${seq}`, role, content: [{ type: 'text', text }] },
}) + '\n';
const texts = (ev: TailEvent) => ev.items.map((i: any) => i.text);
const quiet = { read: () => [], sig: () => '' };

function log(n: number): string {
  const f = join(dir, `log-${Date.now()}-${Math.random().toString(36).slice(2)}.jsonl`);
  let s = line('hello', 'user');
  for (let i = 1; i < n; i++) s += line(`reply ${i}`);
  writeFileSync(f, s);
  return f;
}

test('two subscribers on one tailer each get a snapshot of their own limit, and again after a reset', () => {
  let f = log(20);
  let sid = 's-1';
  const t = new TranscriptTailer({ agentId: 'zz-win', resolve: () => ({ path: f, sid }), limit: 40, interrupted: quiet });
  const small: TailEvent[] = [], big: TailEvent[] = [];
  t.subscribe((e) => small.push(e), undefined, 3);
  t.subscribe((e) => big.push(e), undefined, 10);
  assert.equal(small[0].type, 'snapshot'); assert.equal(big[0].type, 'snapshot');
  assert.deepEqual(texts(small[0]), ['reply 17', 'reply 18', 'reply 19']);
  assert.equal(big[0].items.length, 10);
  assert.ok((big[0].render_items?.length ?? 0) > 0);
  // a rotation: both get a reset snapshot, each its own size
  f = log(12); sid = 's-2';
  t.tick();
  assert.equal(small.at(-1)!.type, 'snapshot'); assert.equal(small.at(-1)!.reset, true);
  assert.equal(small.at(-1)!.items.length, 3);
  assert.equal(big.at(-1)!.items.length, 10);
  assert.equal(big.at(-1)!.reset, true);
});

test('a subscriber without a limit of its own gets the tailer default', () => {
  const f = log(8);
  const t = new TranscriptTailer({ agentId: 'zz-win', resolve: () => ({ path: f, sid: 's' }), limit: 5, interrupted: quiet });
  const got: TailEvent[] = [];
  t.subscribe((e) => got.push(e));
  assert.equal(got[0].items.length, 5);
});

test('a resume that missed more than the subscriber limit is a snapshot of that limit, not a long delta', () => {
  const f = log(5);
  const t = new TranscriptTailer({ agentId: 'zz-win', resolve: () => ({ path: f, sid: 's' }), limit: 40, interrupted: quiet });
  const first: TailEvent[] = [];
  const unsub = t.subscribe((e) => first.push(e), undefined, 40);
  const lastSeen = first[0].id;                       // the client knows 5 items, then disconnects (an API restart)
  unsub();
  for (let i = 0; i < 30; i++) appendFileSync(f, line(`late ${i}`));
  // a resume with limit 10: 30 items missed > 10 -> a snapshot of the newest 10, never a 30-item delta
  const back: TailEvent[] = [];
  t.subscribe((e) => back.push(e), lastSeen, 10);
  assert.equal(back.length, 1);
  assert.equal(back[0].type, 'snapshot');
  assert.equal(back[0].items.length, 10);
  assert.equal(back[0].items.at(-1).text, 'late 29');
  // the same resume with limit 40: the 30 missed items fit -> a delta of exactly them
  const wide: TailEvent[] = [];
  t.subscribe((e) => wide.push(e), lastSeen, 40);
  assert.equal(wide[0].type, 'delta');
  assert.equal(wide[0].items.length, 30);
  assert.equal(wide[0].items[0].text, 'late 0');
});

test('a growth larger than a subscriber limit reaches it as its snapshot while a wider subscriber gets the delta', () => {
  const f = log(5);
  const t = new TranscriptTailer({ agentId: 'zz-win', resolve: () => ({ path: f, sid: 's' }), limit: 40, interrupted: quiet });
  const small: TailEvent[] = [], big: TailEvent[] = [];
  t.subscribe((e) => small.push(e), undefined, 4);
  t.subscribe((e) => big.push(e), undefined, 40);
  for (let i = 0; i < 12; i++) appendFileSync(f, line(`burst ${i}`));
  t.tick();
  assert.equal(small.at(-1)!.type, 'snapshot', 'twelve new items do not fit a window of four');
  assert.equal(small.at(-1)!.reset, undefined, 'not a reset: the session is the same');
  assert.deepEqual(texts(small.at(-1)!), ['burst 8', 'burst 9', 'burst 10', 'burst 11']);
  assert.equal(big.at(-1)!.type, 'delta');
  assert.equal(big.at(-1)!.items.length, 12);
  // a small growth is a delta for both
  appendFileSync(f, line('one more'));
  t.tick();
  assert.equal(small.at(-1)!.type, 'delta'); assert.deepEqual(texts(small.at(-1)!), ['one more']);
  assert.equal(big.at(-1)!.type, 'delta');
});
