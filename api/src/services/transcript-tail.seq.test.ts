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
const { TranscriptTailer, tailerCapForTests, alignShift } = await import('./transcript-tail.js');
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
  assert.equal(got[0].id, 's~h1:4', 'below the cap the id is the item count, under the head cut tag (STREAM_CUT)');
  appendFileSync(f, line('x 1') + line('x 2') + line('x 3'));
  t.tick();
  assert.equal(got[1].type, 'delta');
  assert.deepEqual(texts(got[1]), ['x 1', 'x 2', 'x 3']);
  assert.equal(got[1].id, 's~h1:7');
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

test('at the cap, a window that shrinks as it slides (a line of two items left the front) is not a truncation', () => {
  tailerCapForTests(CAP);
  // An assistant line with text and a tool call is two items with one uuid; a window never starts inside a line, so
  // the kept window is one longer while such a line sits at its front, and one shorter when the line slides out.
  const both = (text: string) => JSON.stringify({
    type: 'assistant', uuid: `a-${++n}`, timestamp: T(n),
    message: { id: `m-${n}`, role: 'assistant', content: [{ type: 'text', text }, { type: 'tool_use', id: `t-${n}`, name: 'Bash', input: { command: 'ls' } }] },
  }) + '\n';
  const f = log(2);
  for (let i = 0; i < 6; i++) appendFileSync(f, i % 2 ? both(`pair ${i}`) : line(`one ${i}`));
  const t = tailer(f);
  const got: TailEvent[] = [];
  t.subscribe((e) => got.push(e), undefined, 40);
  const sent: string[] = [];
  for (let i = 0; i < 6; i++) {
    appendFileSync(f, i % 2 ? line(`new ${i}`) : both(`new ${i}`));
    t.tick();
    sent.push(`new ${i}`);
  }
  const later = got.slice(1);
  assert.ok(later.every((e) => e.type === 'delta'), `every tick is a delta: ${later.map((e) => `${e.type}${e.reset ? '/reset' : ''}`).join(' ')}`);
  assert.deepEqual(later.flatMap(texts).filter(Boolean), sent, 'each new line\'s text delivered once, in order');
  assert.equal(new Set(got.map((e) => tagOf(e.id))).size, 1, 'one numbering');
  for (let i = 1; i < got.length; i++) assert.ok(seqOf(got[i].id) > seqOf(got[i - 1].id));
});

test('alignment finds the last kept item where it moved to, not a copy of it among the new items', () => {
  const it = (k: string) => ({ kind: 'text', role: 'assistant', text: k });  // no uuid: two items can share a key
  const prev = ['a', 'b', 'w', 'x'].map(it);
  assert.equal(alignShift(prev, ['w', 'x', 'y', 'x'].map(it)), -2, 'slid by two; the new x is not the kept one');
  assert.equal(alignShift(prev, ['b', 'w', 'x', 'x'].map(it)), -1, 'slid by one; the new x is not the kept one');
  assert.equal(alignShift(prev, ['a', 'b', 'w', 'x', 'x'].map(it)), 0, 'below the cap');
  const long = ['a', 'b', 'c', 'd', 'w', 'x'].map(it);
  assert.equal(alignShift(long, ['q', 'b', 'c', 'd', 'w', 'x', 'y'].map(it)), 0, 'only the items before the last one must line up');
  assert.equal(alignShift(prev, ['w', 'x', 'y'].map(it)), -2, 'two keys line up at the window front');
  assert.equal(alignShift(prev, ['x', 'y', 'z'].map(it)), null, 'one key alone at the window front is not a match');
  assert.equal(alignShift(prev, ['p', 'q'].map(it)), null);
});

test('below the cap the id stays the item count after a line lands before the tail', () => {
  const f = join(dir, `cut-${Date.now()}.jsonl`);
  // the line lands far enough back (more than the items alignment checks) that the windows still line up around it
  writeFileSync(f, line('hello', 'user') + line('working') + line('again', 'user') + [1, 2, 3, 4, 5].map((i) => line(`done ${i}`)).join(''));
  const box = { turns: [] as any[], sig: '' };
  const t = new TranscriptTailer({ agentId: 'zz-seq', resolve: () => ({ path: f, sid: 's' }), limit: 40,
    interrupted: { read: () => box.turns, sig: () => box.sig } });
  const got: TailEvent[] = [];
  t.subscribe((e) => got.push(e), undefined, 40);
  assert.equal(seqOf(got[0].id), 8);
  box.turns = [{ id: 'x1', text: 'hello', at: T(1) }]; box.sig = '1';   // the first turn was cut: its line lands before 'again'
  t.tick();
  const ev = got.at(-1)!;
  assert.equal(ev.type, 'snapshot');
  assert.equal(ev.items.length, 9);
  assert.equal(seqOf(ev.id), 9, 'another tailer counts the same log the same way');
});

test('below the cap, a log that shrank and grew back is a new numbering: an old id is never caught up', () => {
  const f = log(6);
  const t = tailer(f);
  const keep: TailEvent[] = [];
  t.subscribe((e) => keep.push(e), undefined, 40);
  const old = keep[0].id;                               // a client that knew 6 items, then dropped
  writeFileSync(f, line('fresh start', 'user') + line('fresh 1') + line('fresh 2'));
  t.tick();
  assert.equal(keep.length, 2, 'one snapshot for the shrink');
  assert.equal(keep[1].type, 'snapshot', 'never the shrunk window as a delta');
  assert.equal(keep[1].reset, true);
  assert.notEqual(tagOf(keep[1].id), tagOf(old), 'a new tag');
  t.tick();
  assert.equal(keep.length, 2, 'no snapshot on the next beat');
  appendFileSync(f, line('fresh 3') + line('fresh 4') + line('fresh 5'));
  t.tick();                                             // 6 items again, none of them the ones the client had
  assert.equal(keep.at(-1)!.type, 'delta', 'growth after the shrink is a plain delta');
  const back: TailEvent[] = [];
  t.subscribe((e) => back.push(e), old, 40);
  assert.equal(back.length, 1, 'not "caught up"');
  assert.equal(back[0].type, 'snapshot');
  assert.equal(back[0].items.at(-1).text, 'fresh 5');
});

test('a record written out of order, landing well before the tail, reaches the subscribers', () => {
  // A Codex rollout is read in ordinal order (more than one writer can append), so a late record lands in place.
  const rec = (o: number, payload: any, type = 'response_item') =>
    JSON.stringify({ ordinal: o, timestamp: `2026-08-27T10:00:${String(o).padStart(2, '0')}Z`, type, payload }) + '\n';
  const msg = (o: number, role: string, text: string) =>
    rec(o, { id: `m-${o}`, type: 'message', role, content: [{ type: role === 'user' ? 'input_text' : 'output_text', text }] });
  const f = join(dir, `rollout-${Date.now()}.jsonl`);
  let s = rec(0, { id: '00000000-0000-7000-8000-000000000001', timestamp: '2026-08-27T10:00:00Z', cwd: '/tmp' }, 'session_meta')
    + rec(1, { cwd: '/tmp', model: 'm' }, 'turn_context') + msg(2, 'user', 'start');
  for (let o = 3; o <= 12; o++) if (o !== 5) s += msg(o, 'assistant', `said ${o}`);
  writeFileSync(f, s);
  const t = tailer(f);
  const got: TailEvent[] = [];
  t.subscribe((e) => got.push(e), undefined, 40);
  appendFileSync(f, msg(5, 'assistant', 'said 5 late'));
  t.tick();
  assert.equal(got.length, 2, 'the late record is sent');
  assert.equal(got[1].type, 'snapshot', 'in place: a delta only appends');
  assert.deepEqual(texts(got[1]).slice(0, 5), ['start', 'said 3', 'said 4', 'said 5 late', 'said 6']);
  assert.equal(tagOf(got[1].id), `${tagOf(got[0].id)}~1`, 'one revision: an id from before it is no longer caught up');
  t.tick();
  assert.equal(got.length, 2, 'no snapshot on the next beat');
  appendFileSync(f, msg(13, 'assistant', 'said 13'));
  t.tick();
  assert.equal(got[2].type, 'delta');
  assert.deepEqual(texts(got[2]), ['said 13']);
});

test('at the cap, a beat of ordinary (uuid-bearing) items that slides all but one kept item is still a delta', () => {
  tailerCapForTests(CAP);
  for (let k = 1; k < CAP; k++) {
    const f = log(12);
    const t = tailer(f);
    const got: TailEvent[] = [];
    t.subscribe((e) => got.push(e), undefined, 40);
    const fresh = Array.from({ length: k }, (_, i) => `burst ${k}.${i}`);
    appendFileSync(f, fresh.map((x) => line(x)).join(''));
    t.tick();
    assert.equal(got.length, 2, `${k} new`);
    assert.equal(got[1].type, 'delta', `${k} new items: a delta, not a snapshot`);
    assert.deepEqual(texts(got[1]), fresh);
    assert.equal(tagOf(got[1].id), tagOf(got[0].id), 'the same numbering');
  }
});

// A Codex rollout, read in ordinal order: `rollout(...)` writes records, `crec` is one message record.
const crec = (o: number, id: string, role: string, text: string) =>
  JSON.stringify({ ordinal: o, timestamp: `2026-08-27T10:00:${String(o).padStart(2, '0')}Z`, type: 'response_item',
    payload: { id, type: 'message', role, content: [{ type: role === 'user' ? 'input_text' : 'output_text', text }] } }) + '\n';
function rollout(body: string): string {
  const f = join(dir, `rollout-${Date.now()}-${Math.random().toString(36).slice(2)}.jsonl`);
  writeFileSync(f, JSON.stringify({ ordinal: 0, timestamp: '2026-08-27T10:00:00Z', type: 'session_meta',
    payload: { id: '00000000-0000-7000-8000-000000000002', timestamp: '2026-08-27T10:00:00Z', cwd: '/tmp' } }) + '\n'
    + JSON.stringify({ ordinal: 1, timestamp: '2026-08-27T10:00:01Z', type: 'turn_context', payload: { cwd: '/tmp', model: 'm' } }) + '\n'
    + body);
  return f;
}

test('a late record that repeats an item already sent, placed before the tail, is still sent', () => {
  const f = rollout(crec(2, 'u-1', 'user', 'start') + crec(3, 'm-3', 'assistant', 'same words')
    + [6, 7, 8, 9, 10].map((o) => crec(o, `m-${o}`, 'assistant', `said ${o}`)).join(''));
  const t = tailer(f);
  const got: TailEvent[] = [];
  t.subscribe((e) => got.push(e), undefined, 40);
  appendFileSync(f, crec(4, 'm-3', 'assistant', 'same words'));   // a second copy of a sent item's key, written late
  t.tick();
  assert.equal(got.length, 2, 'the copy is sent');
  assert.equal(got[1].type, 'snapshot');
  assert.deepEqual(texts(got[1]).slice(0, 4), ['start', 'same words', 'same words', 'said 6']);
});

test('below the cap a window never slides: a match that says it did is a restate, not a delta', () => {
  // The whole log is two items with one key; a record written late lands between them. The last kept item's key
  // matches the window front, which below the cap cannot be where it moved to.
  const f = rollout(crec(2, 'm-x', 'assistant', 'twice') + crec(4, 'm-x', 'assistant', 'twice'));
  const t = tailer(f);
  const got: TailEvent[] = [];
  t.subscribe((e) => got.push(e), undefined, 40);
  assert.deepEqual(texts(got[0]), ['twice', 'twice']);
  appendFileSync(f, crec(3, 'm-i', 'assistant', 'between'));
  t.tick();
  assert.equal(got.length, 2);
  assert.equal(got[1].type, 'snapshot', 'never a delta that repeats an item the client has');
  assert.deepEqual(texts(got[1]), ['twice', 'between', 'twice']);
});

test('below the cap, a new log file under the same session is a new numbering: an old id is never caught up', () => {
  for (const viaGone of [false, true]) {                // the path swaps directly, or goes away and comes back
    const first = log(6);
    let path: string | null = first;
    const t = new TranscriptTailer({ agentId: 'zz-seq', resolve: () => ({ path, sid: 's' }), limit: 40, interrupted: quiet });
    const keep: TailEvent[] = [];
    t.subscribe((e) => keep.push(e), undefined, 40);
    const old = keep[0].id;                             // a client that knew the first file's 6 items, then dropped
    if (viaGone) { path = null; t.tick(); }
    const second = log(3);
    path = second;
    t.tick();
    assert.equal(keep.at(-1)!.type, 'snapshot');
    assert.equal(keep.at(-1)!.reset, true);
    appendFileSync(second, line('more 1') + line('more 2') + line('more 3'));
    t.tick();                                           // 6 items again, none of them the client's
    const back: TailEvent[] = [];
    t.subscribe((e) => back.push(e), old, 40);
    assert.equal(back.length, 1, `not "caught up" (${viaGone ? 'via no file' : 'direct swap'})`);
    assert.equal(back[0].type, 'snapshot');
    assert.equal(back[0].items.at(-1).text, 'more 3');
  }
});
