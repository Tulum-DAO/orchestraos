/**
 * Stream ids are SEQUENCE numbers, never window offsets. The tailer keeps at most its cap of items per log; at the cap
 * the kept window slides (one in, one out) and its length stops growing. With ids that were window offsets, a tick that
 * slid sent nothing and a resume at the cap read as caught up: a seat whose log passed the cap stopped streaming until
 * an API restart. Every item the tailer has seen now has the next number; an event's id names its last item's number.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/services/transcript-tail.seq.test.ts (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync, appendFileSync } from 'node:fs';
import { join } from 'node:path';

const dir = testTmpDir('tail-seq-');
process.env.ORCHESTRA_DIR = join(dir, 'orch');
process.env.HOME = join(dir, 'home');
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
const { TranscriptTailer, tailerCapForTests } = await import('./transcript-tail.js');
type TailEvent = import('./transcript-tail.js').TailEvent;

const CAP = 5;
afterEach(() => tailerCapForTests(null));

let n = 0;
const T = (k: number) => new Date(Date.UTC(2026, 10, 26, 9, 0, k % 60, Math.floor(k / 60))).toISOString();
const line = (text: string, role: 'user' | 'assistant' = 'assistant') => JSON.stringify({
  type: role, uuid: `${role[0]}-${++n}`, timestamp: T(n),
  message: role === 'user' ? { role, content: text } : { id: `m-${n}`, role, content: [{ type: 'text', text }] },
}) + '\n';
const texts = (ev: TailEvent) => ev.items.map((i: any) => i.text);
const quiet = { read: () => [], sig: () => '' };
const tagOf = (id: string) => id.slice(0, id.lastIndexOf(':'));
const seqOf = (id: string) => Number(id.slice(id.lastIndexOf(':') + 1));

function log(count: number): string {
  const f = join(dir, `log-${Date.now()}-${Math.random().toString(36).slice(2)}.jsonl`);
  let s = line('hello', 'user');
  for (let i = 1; i < count; i++) s += line(`reply ${i}`);
  writeFileSync(f, s);
  return f;
}
const tailer = (f: string) =>
  new TranscriptTailer({ agentId: 'zz-seq', resolve: () => ({ path: f, sid: 's' }), limit: 40, interrupted: quiet });

test('at the cap, a tick that slid the window still delivers the new items, with strictly increasing ids', () => {
  tailerCapForTests(CAP);
  const f = log(12);
  const t = tailer(f);
  const got: TailEvent[] = [];
  t.subscribe((e) => got.push(e), undefined, 40);
  assert.equal(got[0].type, 'snapshot');
  assert.equal(got[0].items.length, CAP, 'the tailer keeps its cap of items');
  appendFileSync(f, line('new 1'));
  t.tick();
  assert.equal(got.length, 2, 'one in, one out at the cap is still a new item');
  assert.equal(got[1].type, 'delta');
  assert.deepEqual(texts(got[1]), ['new 1']);
  appendFileSync(f, line('new 2') + line('new 3') + line('new 4'));
  t.tick();
  assert.equal(got[2].type, 'delta');
  assert.deepEqual(texts(got[2]), ['new 2', 'new 3', 'new 4']);
  t.tick();                                             // nothing new: nothing sent
  assert.equal(got.length, 3);
  const ids = got.map((e) => e.id);
  assert.equal(new Set(ids.map(tagOf)).size, 1, 'one numbering');
  for (let i = 1; i < ids.length; i++) assert.ok(seqOf(ids[i]) > seqOf(ids[i - 1]), `ids increase: ${ids.join(' ')}`);
  assert.equal(seqOf(ids[2]) - seqOf(ids[0]), 4, 'an id names the number of its last item');
});

test('a growth that crosses the cap in one tick is a delta of exactly the new items', () => {
  tailerCapForTests(CAP);
  const f = log(4);
  const t = tailer(f);
  const got: TailEvent[] = [];
  t.subscribe((e) => got.push(e), undefined, 40);
  assert.equal(got[0].id, 's:4', 'below the cap the id is the item count, as before');
  appendFileSync(f, line('x 1') + line('x 2') + line('x 3'));
  t.tick();
  assert.equal(got[1].type, 'delta');
  assert.deepEqual(texts(got[1]), ['x 1', 'x 2', 'x 3']);
  assert.equal(got[1].id, 's:7');
});

test('a resume at the cap gets exactly what it missed; one whose item slid out of the kept window gets a snapshot', () => {
  tailerCapForTests(CAP);
  const f = log(12);
  const t = tailer(f);
  const keep: TailEvent[] = [];
  t.subscribe((e) => keep.push(e), undefined, 40);      // keeps the tailer reading while the client is away
  const first: TailEvent[] = [];
  const unsub = t.subscribe((e) => first.push(e), undefined, 40);
  const lastSeen = first[0].id;
  unsub();
  appendFileSync(f, line('away 1') + line('away 2'));
  t.tick();
  const back: TailEvent[] = [];
  t.subscribe((e) => back.push(e), lastSeen, 40);
  assert.equal(back.length, 1, 'never "caught up" when two items landed');
  assert.equal(back[0].type, 'delta');
  assert.deepEqual(texts(back[0]), ['away 1', 'away 2']);
  // caught up for real: nothing
  const now: TailEvent[] = [];
  t.subscribe((e) => now.push(e), back[0].id, 40);
  assert.equal(now.length, 0);
  // the window slides past the client's last item: a snapshot, never a delta that skips items
  for (let i = 0; i < CAP + 2; i++) appendFileSync(f, line(`later ${i}`));
  t.tick();
  const stale: TailEvent[] = [];
  t.subscribe((e) => stale.push(e), lastSeen, 40);
  assert.equal(stale.length, 1);
  assert.equal(stale[0].type, 'snapshot');
  assert.equal(stale[0].items.at(-1).text, `later ${CAP + 1}`);
  // an id ahead of the tailer (another numbering) is a snapshot too
  const ahead: TailEvent[] = [];
  t.subscribe((e) => ahead.push(e), `${tagOf(lastSeen)}:${seqOf(keep.at(-1)!.id) + 3}`, 40);
  assert.equal(ahead[0].type, 'snapshot');
});

test('an id from another tailer (an API restart) past the cap gets a snapshot; below the cap it still resumes', () => {
  tailerCapForTests(CAP);
  const big = log(12);
  const a: TailEvent[] = [];
  tailer(big).subscribe((e) => a.push(e), undefined, 40);
  appendFileSync(big, line('after restart 1') + line('after restart 2'));
  const b: TailEvent[] = [];
  tailer(big).subscribe((e) => b.push(e), a[0].id, 40);
  assert.equal(b.length, 1, 'a new tailer cannot know what the old numbering counted');
  assert.equal(b[0].type, 'snapshot');
  // below the cap the numbering starts at the log's first item, so an old id still names the same item
  const small = log(3);
  const c: TailEvent[] = [];
  tailer(small).subscribe((e) => c.push(e), undefined, 40);
  appendFileSync(small, line('small 1'));
  const d: TailEvent[] = [];
  tailer(small).subscribe((e) => d.push(e), c[0].id, 40);
  assert.equal(d[0].type, 'delta');
  assert.deepEqual(texts(d[0]), ['small 1']);
});

test('a subscriber that joins between beats does not take the new items from the ones already there', () => {
  tailerCapForTests(CAP);
  for (const count of [3, 12]) {                        // below the cap and at it
    const f = log(count);
    const t = tailer(f);
    const a: TailEvent[] = [];
    t.subscribe((e) => a.push(e), undefined, 40);
    appendFileSync(f, line('between 1') + line('between 2'));
    t.subscribe(() => {}, undefined, 40);               // its read on connect finds the two new items first
    t.tick();
    assert.equal(a.length, 2, `the first subscriber still gets them (${count} items)`);
    assert.equal(a[1].type, 'delta');
    assert.deepEqual(texts(a[1]), ['between 1', 'between 2']);
    assert.equal(seqOf(a[1].id) - seqOf(a[0].id), 2);
  }
});
