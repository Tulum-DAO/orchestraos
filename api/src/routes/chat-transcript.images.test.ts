/**
 * Images in the chat: an agent's reply that names an image file, or a Write that made one, carries
 * `images` (an opaque id per path) on its item and its render node when the file is there under
 * the agent's own roots; a path outside them, or not there, gets no id; the words are untouched.
 * The poll route and the SSE tailer share finishWindow, so normalizeTranscript is the one place
 * to prove the stamp (the tailer's own run is tests/transcript-stream.images.test.ts).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { testTmpDir } from '../lib/testTmp.js';
import { MEDIA_ID, MAX_ROWS_PER_AGENT, MediaStore, imageRefsFor, stamperFor, _fileChecks, _resetMisses } from '../lib/mediaStore.js';

// The data dir (and so the id table and the agent's roots) is read at import, so it is set first.
const DATA = testTmpDir('images-data-');
const PROJECT = join(DATA, 'work', 'gm');
mkdirSync(join(DATA, 'state', 'agents'), { recursive: true });
mkdirSync(join(DATA, 'state', 'shots', 'gm'), { recursive: true });
mkdirSync(join(DATA, 'state', 'uploads'), { recursive: true });
mkdirSync(join(PROJECT, 'shots'), { recursive: true });
writeFileSync(join(DATA, 'state', 'agents', 'gm.json'), JSON.stringify({ cwd: PROJECT }));
process.env.ORCHESTRA_DIR = DATA;
const { normalizeTranscript, stampImages, buildRenderItems } = await import('./chat-transcript.js');

const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(32, 7)]);
const claude = (role: 'user' | 'assistant', content: unknown, ts = '2026-11-22T05:00:00Z') =>
  JSON.stringify({ type: role, uuid: `u-${Math.random()}`, timestamp: ts, message: { role, content } });

test('a reply naming an image under the agent\'s roots carries its opaque id; the words stay', () => {
  const shot = join(PROJECT, 'shots', 'after.png');
  const own = join(DATA, 'state', 'shots', 'gm', 'x.png');
  writeFileSync(shot, PNG);
  writeFileSync(own, PNG);
  const lines = [
    claude('user', 'show me'),
    claude('assistant', `Here it is: ${shot} (and my shot folder's ${own})`),
  ];
  const { items, render_items } = normalizeTranscript(lines, 'gm', 's', 150);
  const reply = items.find((it: any) => it.kind === 'text' && it.role === 'assistant');
  assert.ok(reply.text.includes(shot), 'the path stays readable as text');
  assert.deepEqual(reply.images.map((i: any) => i.path), [shot, own]);
  assert.deepEqual(reply.images.map((i: any) => i.name), ['after.png', 'x.png']);
  for (const i of reply.images) { assert.match(i.id, MEDIA_ID); assert.ok(!i.id.includes('/')); }
  const node = render_items.find((n: any) => n.kind === 'assistant');
  assert.deepEqual(node.images, reply.images);
  const again = normalizeTranscript(lines, 'gm', 's', 150).items.find((it: any) => it.kind === 'text' && it.role === 'assistant');
  assert.equal(again.images[0].id, reply.images[0].id, 'the same path, the same id');
});

test('no id: a path outside the roots, another client\'s upload under the data dir, a file that is not there, a user line, a Read', () => {
  writeFileSync(join(DATA, 'state', 'uploads', 'client-b.png'), PNG);
  const lines = [
    claude('user', `look at ${join(PROJECT, 'a.png')}`),
    claude('assistant', [
      { type: 'text', text: `See /etc/wallpaper.png, ${join(DATA, 'state', 'uploads', 'client-b.png')} and ${join(PROJECT, 'not-yet.png')}` },
      { type: 'tool_use', id: 't1', name: 'Read', input: { file_path: join(PROJECT, 'shots', 'after.png') } },
    ]),
    claude('user', [{ type: 'tool_result', tool_use_id: 't1', content: 'bytes' }]),
  ];
  const { items, render_items } = normalizeTranscript(lines, 'gm', 's', 150);
  for (const it of items) assert.equal('images' in it, false, JSON.stringify(it.kind));
  for (const n of render_items) assert.equal('images' in n, false, n.kind);
});

test('a Write that made an image carries it on the tool item and its render node', () => {
  const made = join(PROJECT, 'chart.png');
  writeFileSync(made, PNG);
  const lines = [
    claude('user', 'plot it'),
    claude('assistant', [{ type: 'tool_use', id: 't1', name: 'Write', input: { file_path: made, content: '...' } }]),
    claude('user', [{ type: 'tool_result', tool_use_id: 't1', content: 'ok' }]),
    claude('assistant', [{ type: 'tool_use', id: 't2', name: 'Write', input: { file_path: join(PROJECT, 'notes.md'), content: '#' } }]),
    claude('user', [{ type: 'tool_result', tool_use_id: 't2', content: 'ok' }]),
  ];
  const { items, render_items } = normalizeTranscript(lines, 'gm', 's', 150);
  const [w1, w2] = items.filter((it: any) => it.kind === 'tool_use');
  assert.equal(w1.images.length, 1);
  assert.equal(w1.images[0].path, made);
  assert.equal('images' in w2, false);
  const [n1, n2] = render_items.filter((n: any) => n.kind === 'tool');
  assert.deepEqual(n1.images, w1.images);
  assert.equal('images' in n2, false);
});

test('stampImages with an injected stamper: it decides, items are never dropped or reordered', () => {
  const asked: string[][] = [];
  const refsFor = (_a: string, paths: string[]) => { asked.push(paths); return paths.filter((p) => p.startsWith('/srv/p/')).map((p) => ({ id: 'f'.repeat(32), path: p, name: p.split('/').pop()! })); };
  const items = [
    { kind: 'text', role: 'assistant', text: 'in /srv/p/a.png, out /srv/q/b.png' },
    { kind: 'thinking', role: 'assistant', text: '/srv/p/think.png' },
    { kind: 'text', role: 'user', text: '/srv/p/user.png' },
    { kind: 'rotation' },
  ];
  const out = stampImages(items, 'gm', refsFor);
  assert.equal(out.length, items.length);
  assert.deepEqual(out[0].images, [{ id: 'f'.repeat(32), path: '/srv/p/a.png', name: 'a.png' }]);
  assert.deepEqual(asked, [['/srv/p/a.png', '/srv/q/b.png']]);
  assert.equal('images' in out[1], false);
  assert.strictEqual(out[2], items[2], 'an untouched item is the same object');
  assert.strictEqual(out[3], items[3]);
  const nodes = buildRenderItems(out);
  assert.deepEqual(nodes[0].images, out[0].images);
  assert.equal('images' in nodes[1], false);
});

// F1 (#598 re-review): above the per-agent cap, ids churned on EVERY stamp (0/600 stable). The window is read first
// now: its newest `cap` paths are what the table keeps, no row it shows is evicted, and every id is the same next time.
function bigWindow(dir: string, n: number, start = 0) {
  const items: any[] = [];
  for (let i = start; i < start + n; i++) {
    const p = join(dir, `step-${String(i).padStart(4, '0')}.png`);
    writeFileSync(p, PNG);
    items.push({ kind: 'text', role: 'assistant', text: `step ${i}: ${p}`, uuid: `w${i}` });
  }
  return items;
}
const idsOf = (out: any[]) => out.map((it) => (it.images ? it.images[0].id : null));

test('F1: a 600-image window stamped twice keeps stable ids (cap 500: the newest 500 carry one, the oldest 100 none)', () => {
  _resetMisses();
  const dir = join(PROJECT, 'qa-loop');
  mkdirSync(dir, { recursive: true });
  const store = new MediaStore(join(testTmpDir('images-f1-'), 'ids.json'), 500);
  const deps = { store, roots: () => [PROJECT] };
  const refsFor = (a: string, p: string[], w?: ReadonlySet<string>) => imageRefsFor(a, p, deps, w);
  const items = bigWindow(dir, 600);
  const first = idsOf(stampImages(items, 'gm', refsFor));
  const second = idsOf(stampImages(items, 'gm', refsFor));
  assert.deepEqual(second, first, 'every id is the same on the next stamp');
  assert.equal(first.filter(Boolean).length, 500);
  assert.equal(first.slice(0, 100).filter(Boolean).length, 0, 'the oldest go without a thumbnail');
  assert.equal(first.slice(100).filter(Boolean).length, 500, 'the newest 500 all carry one');
  assert.equal(new Set(first.filter(Boolean)).size, 500);
  assert.equal(store.countFor('gm'), 500);
  // The window moves on by ten new images: the 490 still shown keep their ids, the ten new ones get theirs.
  const moved = [...items.slice(10), ...bigWindow(dir, 10, 600)];
  const third = idsOf(stampImages(moved, 'gm', refsFor));
  assert.deepEqual(third.slice(100, 590), first.slice(110), 'ids still in the window are unchanged');
  assert.equal(third.slice(590).filter(Boolean).length, 10);
  assert.deepEqual(idsOf(stampImages(moved, 'gm', refsFor)), third);
});

test('F1: below the cap nothing changes; at the cap a mint that would evict a row the window shows is refused, else the least recently used goes', () => {
  _resetMisses();
  const dir = join(PROJECT, 'qa-small');
  mkdirSync(dir, { recursive: true });
  const store = new MediaStore(join(testTmpDir('images-f1b-'), 'ids.json'), 500);
  const deps = { store, roots: () => [PROJECT] };
  const refsFor = (a: string, p: string[], w?: ReadonlySet<string>) => imageRefsFor(a, p, deps, w);
  const items = bigWindow(dir, 120);
  const a = idsOf(stampImages(items, 'gm', refsFor));
  assert.equal(a.filter(Boolean).length, 120);
  assert.deepEqual(idsOf(stampImages(items, 'gm', refsFor)), a);
  // The table at the cap with rows the current window shows: a mint that would need to evict one of them is refused.
  const tiny = new MediaStore(join(testTmpDir('images-f1c-'), 'ids.json'), 2);
  const x = tiny.mint('gm', '/p/x.png')!;
  const y = tiny.mint('gm', '/p/y.png')!;
  assert.equal(tiny.mint('gm', '/p/z.png', { protect: new Set(['/p/x.png', '/p/y.png']) }), null);
  assert.equal(tiny.lookup(x)?.path, '/p/x.png');
  assert.equal(tiny.lookup(y)?.path, '/p/y.png');
  // Outside the protected set the least recently USED goes (x was looked up after y: y goes).
  tiny.lookup(x);
  assert.ok(tiny.mint('gm', '/p/z.png', { protect: new Set(['/p/z.png']) }));
  assert.equal(tiny.lookup(y), null);
  assert.ok(tiny.lookup(x));
});

// #601 review finding 4: the cap counted paths that can never be shown, and paging replaced the newest window's ids.
test('SABOTAGE window: 504 newer paths that are not there do not crowd out 8 real images (only showable paths count)', () => {
  _resetMisses();
  const dir = join(PROJECT, 'f4-crowd');
  mkdirSync(dir, { recursive: true });
  const store = new MediaStore(join(testTmpDir('images-f4a-'), 'ids.json'), 500);
  const refsFor = stamperFor({ store, roots: () => [PROJECT] });
  const real = bigWindow(dir, 8);
  const missing = Array.from({ length: 504 }, (_, i) => ({ kind: 'text', role: 'assistant', text: `gone ${join(dir, `gone-${i}.png`)}`, uuid: `g${i}` }));
  const out = idsOf(stampImages([...real, ...missing], 'gm', refsFor));
  assert.equal(out.slice(0, 8).filter(Boolean).length, 8, 'the 8 real images carry ids');
  assert.equal(out.slice(8).filter(Boolean).length, 0);
  assert.deepEqual(idsOf(stampImages([...real, ...missing], 'gm', refsFor)), out, 'and keep them');
});

function timedWindow(dir: string, tag: string, n: number, t0: number) {
  const items = bigWindow(dir, n).map((it, i) => ({ ...it, text: it.text.replace('step-', `${tag}-`), ts: new Date(t0 + i * 1000).toISOString() }));
  for (const it of items) writeFileSync(it.text.slice(it.text.indexOf('/')), PNG);
  return items;
}

test('SABOTAGE paging: an older full window and back keeps every id of the newest window', () => {
  _resetMisses();
  const dir = join(PROJECT, 'f4-page');
  mkdirSync(dir, { recursive: true });
  const store = new MediaStore(join(testTmpDir('images-f4b-'), 'ids.json'), 500);
  const refsFor = stamperFor({ store, roots: () => [PROJECT] });
  const T = Date.parse('2026-11-22T00:00:00Z');
  const older = timedWindow(dir, 'old', 500, T);
  const newest = timedWindow(dir, 'new', 500, T + 3_600_000);
  const live = idsOf(stampImages(newest, 'gm', refsFor));
  assert.equal(live.filter(Boolean).length, 500);
  const page = idsOf(stampImages(older, 'gm', refsFor));
  assert.equal(page.filter(Boolean).length, 0, 'the table is full of rows referenced later: the older page takes none of them');
  assert.deepEqual(idsOf(stampImages(newest, 'gm', refsFor)), live, 'back at the live end: 500/500 of the newest ids kept');
  assert.equal(store.countFor('gm'), 500);
  // When the live end moves on, the rows referenced longest ago are the ones that go.
  const next = timedWindow(dir, 'next', 10, T + 7_200_000);
  const moved = idsOf(stampImages([...newest.slice(10), ...next], 'gm', refsFor));
  assert.deepEqual(moved.slice(0, 490), live.slice(10));
  assert.equal(moved.slice(490).filter(Boolean).length, 10);
});

test('SABOTAGE paging: a path named again by a newer item moves its "last referenced" on, so an older page cannot evict it', () => {
  _resetMisses();
  const dir = join(PROJECT, 'f4-seen');
  mkdirSync(dir, { recursive: true });
  const store = new MediaStore(join(testTmpDir('images-f4c-'), 'ids.json'), 3);
  const refsFor = stamperFor({ store, roots: () => [PROJECT] });
  const T = Date.parse('2026-11-22T00:00:00Z');
  const at = (name: string, t: number) => {
    const p = join(dir, name);
    writeFileSync(p, PNG);
    return { kind: 'text', role: 'assistant', text: `see ${p}`, uuid: `${name}-${t}`, ts: new Date(t).toISOString() };
  };
  // A first shown on an old page (T), then named again by the newest window (T+3h) with B and C.
  const [a] = idsOf(stampImages([at('a.png', T)], 'gm', refsFor));
  const newest = [at('a.png', T + 3 * 3_600_000), at('b.png', T + 3 * 3_600_000 + 1), at('c.png', T + 3 * 3_600_000 + 2)];
  const live = idsOf(stampImages(newest, 'gm', refsFor));
  assert.equal(live[0], a, 'the same id for A');
  // A page between them (T+1h): every row is referenced later than it, A included.
  assert.deepEqual(idsOf(stampImages([at('d.png', T + 3_600_000)], 'gm', refsFor)), [null]);
  assert.deepEqual(idsOf(stampImages(newest, 'gm', refsFor)), live, 'A, B and C keep their ids');
});

// #602 review finding 5 (perf): a new image was opened three times on its first stamp, and a window naming thousands
// of missing files was walked whole on every poll. Counted with the stamper's file-check counter.
test('SABOTAGE perf: each new image is checked once per stamp; a held one not at all; 5000 missing paths cost a bounded walk', () => {
  _resetMisses();
  const dir = join(PROJECT, 'perf');
  mkdirSync(dir, { recursive: true });
  const store = new MediaStore(join(testTmpDir('images-perf-'), 'ids.json'), 500);
  const refsFor = stamperFor({ store, roots: () => [PROJECT] });
  const items = bigWindow(dir, 20);
  let before = _fileChecks();
  assert.equal(idsOf(stampImages(items, 'gm', refsFor)).filter(Boolean).length, 20);
  assert.equal(_fileChecks() - before, 20, 'one check per new image');
  before = _fileChecks();
  stampImages(items, 'gm', refsFor);
  assert.equal(_fileChecks() - before, 0, 'a held image is not checked again');
  // 5000 missing paths newer than the 20: the walk looks at no more than 2 x cap of them, and the next poll none.
  const missing = Array.from({ length: 5000 }, (_, i) => ({ kind: 'text', role: 'assistant', text: `gone ${join(dir, `miss-${i}.png`)}`, uuid: `m${i}` }));
  before = _fileChecks();
  stampImages([...items, ...missing], 'gm', refsFor);
  assert.ok(_fileChecks() - before <= 2 * MAX_ROWS_PER_AGENT, `bounded walk: ${_fileChecks() - before}`);
  before = _fileChecks();
  stampImages([...items, ...missing], 'gm', refsFor);
  assert.equal(_fileChecks() - before, 0, 'the next poll answers every miss from the cache');
});

// #601 delta review finding 2: the walk's budget counted every distinct path, so 1000 newer paths that cannot be shown
// (not there, or outside the roots) hid the thumbnails of images already shown (999 -> 1/8, 1000 -> 0/8).
test('SABOTAGE budget: 8 images already shown keep their ids behind 1000 newer paths under the roots that are not there', () => {
  _resetMisses();
  const dir = join(PROJECT, 'budget-held');
  mkdirSync(dir, { recursive: true });
  const store = new MediaStore(join(testTmpDir('images-budget-a-'), 'ids.json'), 500);
  const refsFor = stamperFor({ store, roots: () => [PROJECT] });
  const real = bigWindow(dir, 8);
  const shown = idsOf(stampImages(real, 'gm', refsFor));
  assert.equal(shown.filter(Boolean).length, 8);
  const missing = Array.from({ length: 1000 }, (_, i) => ({ kind: 'text', role: 'assistant', text: `gone ${join(dir, `gone-${i}.png`)}`, uuid: `g${i}` }));
  const out = idsOf(stampImages([...real, ...missing], 'gm', refsFor));
  assert.deepEqual(out.slice(0, 8), shown, 'the 8 keep the ids they had');
  assert.equal(out.slice(8).filter(Boolean).length, 0);
  assert.deepEqual(idsOf(stampImages([...real, ...missing], 'gm', refsFor)), out, 'and on the next poll');
});

test('SABOTAGE budget: 1000 newer paths outside the roots cost no budget: 8 real images never shown before get ids at once', () => {
  _resetMisses();
  const dir = join(PROJECT, 'budget-prose');
  mkdirSync(dir, { recursive: true });
  const store = new MediaStore(join(testTmpDir('images-budget-b-'), 'ids.json'), 500);
  const refsFor = stamperFor({ store, roots: () => [PROJECT] });
  const real = bigWindow(dir, 8);
  const prose = Array.from({ length: 1000 }, (_, i) => ({ kind: 'text', role: 'assistant', text: `frame /tmp/frames/f${i}.png`, uuid: `p${i}` }));
  const before = _fileChecks();
  const out = idsOf(stampImages([...real, ...prose], 'gm', refsFor));
  assert.equal(out.slice(0, 8).filter(Boolean).length, 8, 'all 8 shown on the first stamp');
  assert.equal(out.slice(8).filter(Boolean).length, 0);
  assert.equal(_fileChecks() - before, 8, 'only the 8 were opened');
});
