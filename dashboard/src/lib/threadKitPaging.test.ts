import { test } from 'node:test';
import assert from 'node:assert/strict';
import { findOverlap, firstCursor, firstSeqCursor, joinBySeq, joinHead, joinTail, transcriptKey } from './threadKit/paging.ts';
import { liveTextOf } from './transcriptStream.ts';
import type { LiveTextEvent } from './transcript.gen.ts';

type Item = { uuid: string; kind: string; id?: string; tool_use_id?: string; text?: string };
const k = (it: Item) => transcriptKey(it);

/** A conversation where some log lines hold several items (a reply's text and its tool call), as transcripts do. */
function conversation(lines: number): Item[] {
  const out: Item[] = [];
  for (let n = 0; n < lines; n++) {
    const uuid = `line-${n}`;
    if (n % 3 === 0) out.push({ uuid, kind: 'text', text: `message ${n}` });
    else if (n % 3 === 1) { out.push({ uuid, kind: 'text', text: `reply ${n}` }); out.push({ uuid, kind: 'tool_use', id: `tu-${n}` }); }
    else out.push({ uuid, kind: 'tool_result', tool_use_id: `tu-${n - 1}` });
  }
  return out;
}

/** The route's paging, as R2 defines it: the page just older than the cursor's line, never starting inside one
 *  line's items; `next` is the page's first item (its line). */
function serve(all: Item[], before: string, limit: number) {
  const at = all.findIndex((it) => it.uuid === before);
  if (at < 0) return { items: [], hasOlder: false, next: null };
  let start = Math.max(0, at - limit);
  while (start > 0 && all[start - 1].uuid === all[start].uuid) start--;        // keep the oldest line whole
  const items = all.slice(start, at);
  return { items, hasOlder: start > 0, next: items.length ? items[0].uuid : null };
}

test('the live window laid over the list: from where they meet, the live copy wins (a reply that grew)', () => {
  const all = conversation(20);
  const list = all.slice(0, 15);
  const live = all.slice(10).map((it) => (it.uuid === 'line-8' ? { ...it, text: 'grew' } : it));
  assert.equal(findOverlap(list, live, k), 10);
  const j = joinTail(list, live, k);
  assert.equal(j.reset, false);
  assert.deepEqual(j.items.map(k), all.map(k));
  assert.equal(j.items.find((it) => it.uuid === 'line-8' && it.kind === 'text')?.text, live.find((it) => it.uuid === 'line-8' && it.kind === 'text')?.text);
});

test('a live window that does not meet the list is another conversation: it alone', () => {
  const r = joinTail(conversation(5), [{ uuid: 'other-0', kind: 'text' }], k);
  assert.deepEqual([r.reset, r.items.length], [true, 1]);
  assert.deepEqual(joinTail([], conversation(2), k).items.length, 3);
});

test('an older page above a window that starts partway through a line: that line once, whole, no duplicates', () => {
  const all = conversation(12);
  const cut = all.findIndex((it) => it.kind === 'tool_use');          // the window starts at a line's second item
  const live = all.slice(cut);
  const cursor = firstCursor(live);
  assert.notEqual(cursor, live[0].uuid);                               // paged from the first whole line
  const page = serve(all, cursor!, 100);
  const joined = joinHead(page.items, live, k);
  assert.deepEqual(joined.map(k), all.map(k));
  assert.equal(new Set(joined.map(k)).size, joined.length);
});

test('paged to the start by the route\'s own cursor while the live tail keeps arriving: every item once, in order', () => {
  const all = conversation(300);
  let total = 250;                                       // the chat so far; more arrives between pages
  let live = all.slice(Math.max(0, all.findIndex((it) => it.uuid === `line-${total}`) - 150), all.findIndex((it) => it.uuid === `line-${total}`));
  live = live.slice(1);                                  // a window whose top is cut through a line
  let head: Item[] = [];
  let cursor: string | null = null;
  let hasOlder = true, pages = 0;
  while (hasOlder && pages < 50) {
    const known = joinTail(head, live, k).items;         // the hook: the live window over what it has
    const from = cursor ?? firstCursor(known);
    const page = serve(all, from!, 40);
    head = joinHead(page.items, known, k);
    cursor = page.next; hasOlder = page.hasOlder && !!page.next; pages++;
    total += 3;                                          // new lines arrive; the live window moves down
    const end = all.findIndex((it) => it.uuid === `line-${total}`);
    live = all.slice(end - 150, end);
    head = joinTail(head, live, k).items;                // the hook keeps what the window moved past
  }
  const joined = joinTail(head, live, k).items;
  const end = all.findIndex((it) => it.uuid === `line-${total}`);
  assert.deepEqual(joined.map(k), all.slice(0, end).map(k));
  assert.equal(new Set(joined.map(k)).size, joined.length);
  assert.ok(pages > 3);
});

test('the first cursor is the first item that starts a line of its own; none without a nameable item', () => {
  assert.equal(firstCursor([{ uuid: 'a', kind: 'tool_use' }, { uuid: 'b', kind: 'text' }]), 'b');
  assert.equal(firstCursor([{ uuid: 'a', kind: 'text' }]), 'a');
  assert.equal(firstCursor([{ key: 'msg:1', kind: 'queued_batch' } as never, { uuid: 'x', kind: 'text' }]), 'x');
  assert.equal(firstCursor([]), null);
});

test('an empty page changes nothing', () => {
  const list = conversation(3);
  assert.equal(joinHead([], list, k), list);
});

test('live words, the live tail and older pages: each drawn once, the live words after everything', () => {
  // The chat checks the live words against its live window; older pages added above never hold a line still being
  // written, and the line that just finished is in the window (so it is not drawn twice), however many pages load.
  const all = conversation(60);
  const live = all.slice(40);
  let list = live;
  list = joinHead(all.slice(20, 41), list, k);
  list = joinHead(all.slice(0, 20), list, k);
  assert.deepEqual(list.map(k), all.map(k));
  const finished = all[all.length - 1];
  const words: LiveTextEvent = { agent_id: 'pm-demo', message_id: 'msg_example', text: '', done: false,
    blocks: [{ uuid: finished.uuid, text: 'Already a line.' }, { uuid: null, text: 'Still writing' }] };
  assert.deepEqual(liveTextOf(words, live as never), { text: 'Still writing', blocks: [{ uuid: null, text: 'Still writing' }] });
  assert.deepEqual(liveTextOf(words, list as never), liveTextOf(words, live as never), 'the pages change nothing about the live words');
});

// --- a numbered thread (Arturo's: each stored turn has its seq; the newest may be the view's own, not stored yet) ---

type Turn = { seq?: number; text: string };
const seqOf = (t: Turn) => t.seq;
const stored = (n: number, from = 0): Turn[] => Array.from({ length: n }, (_, i) => ({ seq: from + i + 1, text: `turn ${from + i + 1}` }));

test('a numbered thread: older pages, then the window from its first stored turn; its own unstored turns stay last', () => {
  const all = stored(30);
  const window: Turn[] = [...all.slice(20), { text: 'just sent' }, { text: 'streaming reply' }];
  const pages = all.slice(10, 20);
  const { items, reset } = joinBySeq(pages, window, seqOf);
  assert.equal(reset, false);
  assert.deepEqual(items.map((t) => t.text), [...all.slice(10).map((t) => t.text), 'just sent', 'streaming reply']);
});

test('the window re-read after a job posted into the thread: the earlier copy goes, nothing twice, the pages stay', () => {
  const all = stored(40);
  // What the hook holds: pages 1..20, then the window as it was (21..30, plus two turns this view added).
  const held: Turn[] = [...all.slice(0, 30), { text: 'just sent' }, { text: 'its reply' }];
  // Re-read: those two are stored now (31, 32), and more came after (to 40); the newest page starts at 21.
  const reread = all.slice(20, 40);
  const { items } = joinBySeq(held, reread, seqOf);
  assert.deepEqual(items.map((t) => t.seq), all.map((t) => t.seq));
});

test('a window with nothing stored (a new thread) drops the old pages', () => {
  const { items, reset } = joinBySeq(stored(10), [{ text: 'hello' }], seqOf);
  assert.equal(reset, true);
  assert.deepEqual(items.map((t) => t.text), ['hello']);
  assert.equal(firstSeqCursor(seqOf)([{ text: 'hello' }]), null);
  assert.equal(firstSeqCursor(seqOf)([{ text: 'x' }, ...stored(3, 7)]), '8');
});

test('paged to the start of a numbered thread while the view keeps adding turns: every turn once, in order', () => {
  const all = stored(250);
  // The route's paging (thread_store get_thread_page): the `limit` turns just older than seq `before`.
  const route = (before: number, limit = 100) => {
    const older = all.filter((t) => t.seq! < before);
    const page = older.slice(-limit);
    return { items: page, hasOlder: older.length > limit, next: page.length ? String(page[0].seq) : null };
  };
  let live: Turn[] = all.slice(-100);
  let list: Turn[] = [];
  let cursor = firstSeqCursor(seqOf)(live);
  for (let guard = 0; cursor && guard < 10; guard++) {
    const page = route(Number(cursor));
    list = joinHead(page.items, joinBySeq(list, live, seqOf).items, (t) => (t.seq !== undefined ? `s${t.seq}` : 'local'));
    live = [...live, { text: `sent while paging ${guard}` }];            // the view goes on between pages
    cursor = page.hasOlder ? page.next : null;
  }
  const items = joinBySeq(list, live, seqOf).items;
  const seqs = items.map((t) => t.seq).filter((n) => n !== undefined);
  assert.deepEqual(seqs, all.map((t) => t.seq));
  assert.equal(items.filter((t) => t.seq === undefined).length, 2, 'two pages read: the view\'s own turns once each, after the stored ones');
  assert.ok(items.slice(-2).every((t) => t.seq === undefined));
});

// --- a seat that has compacted: notices (cmp-<n>) and markers between conversations (rot:<session>) in the items ---

test('the first cursor skips a compaction notice and a marker between conversations: the first real line', () => {
  const win = [
    { kind: 'compaction', id: 'cmp-2', uuid: 'cmp-2', state: 'compacted' },
    { kind: 'rotation', role: 'system', uuid: 'rot:s-example-3' },
    { kind: 'text', role: 'user', uuid: 'line-40', text: 'Sample question' },
    { kind: 'text', role: 'assistant', uuid: 'line-41', text: 'Example answer' },
  ];
  assert.equal(firstCursor(win), 'line-41');            // line-40 may be cut (the window's top): the next whole line
  assert.equal(firstCursor(win.slice(0, 2)), null);      // nothing a cursor can name
  assert.equal(firstCursor([win[0], win[2]]), 'line-40');
});

test('paged back across five generations to the first message: every line, notice and marker once, in order', () => {
  // Five generations of 60 lines each; each later generation starts with a marker and the notice of its switch.
  type It = { kind: string; uuid: string; ts: number; id?: string; text?: string; role?: string };
  const all: It[] = [];
  let t = 0;
  for (let g = 1; g <= 5; g++) {
    if (g > 1) {
      all.push({ kind: 'compaction', id: `cmp-${g}`, uuid: `cmp-${g}`, ts: t++ });
      all.push({ kind: 'rotation', role: 'system', uuid: `rot:s-${g}`, ts: t++ });
    }
    for (let n = 0; n < 60; n++) all.push({ kind: 'text', role: n % 2 ? 'assistant' : 'user', uuid: `g${g}-${n}`, ts: t++, text: `Sample ${g}.${n}` });
  }
  all[0].text = 'From now on, end every reply with the Example word.';
  // The route: pages only from a real line's uuid (anything else: unknown cursor). A page holds the `limit` lines
  // before it, with the notices and markers in its time range.
  const route = (before: string, limit = 40) => {
    const at = all.findIndex((x) => x.uuid === before && x.kind === 'text');
    if (at < 0) return { items: [] as It[], hasOlder: false, next: null as string | null, error: 'unknown cursor' };
    let start = at, lines = 0;
    while (start > 0 && lines < limit) { start--; if (all[start].kind === 'text') lines++; }
    const page = all.slice(start, at);
    const firstLine = page.find((x) => x.kind === 'text');
    return { items: page, hasOlder: start > 0, next: firstLine ? firstLine.uuid : null };
  };
  // Two live windows: one deep in the last generation, and one whose top is the last line before a switch (its notice
  // and marker then come in the window AND in the first older page).
  for (const from of [all.length - 70, all.findIndex((x) => x.uuid === 'g4-59')]) {
    const live = all.slice(from);
    let list: It[] = [];
    let cursor = firstCursor(live);
    for (let guard = 0; cursor && guard < 20; guard++) {
      const page = route(cursor);
      assert.ok(!('error' in page && page.error), `cursor ${cursor} is a real line`);
      list = joinHead(page.items, joinTail(list, live, k as never).items, k as never);
      cursor = page.hasOlder ? page.next : null;
    }
    const items = joinTail(list, live, k as never).items as It[];
    assert.equal(items[0].text, 'From now on, end every reply with the Example word.');
    assert.deepEqual(items.map((x) => x.uuid), all.map((x) => x.uuid));
  }
});
