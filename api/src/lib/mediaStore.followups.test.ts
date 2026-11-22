/**
 * #598 re-review follow-ups on the allow-list and the stamper (lib/mediaStore.ts):
 *   F2  a working folder that IS the data dir, or encloses it (a seat started in $HOME), is not a
 *       root: state/uploads (every client's) must not reopen through it.
 *   F4  the miss cache is bounded: expired keys go first, then the oldest.
 * And the table's load keeps its bound and its row fields.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, symlinkSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { testTmpDir } from './testTmp.js';
import { MAX_MISSES, MediaStore, agentMediaRoots, enclosesDataDir, imageRefsFor, _missCount, _resetMisses } from './mediaStore.js';

const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(32, 1)]);

function tree() {
  const home = testTmpDir('media-f2-');
  const data = join(home, '.orchestra');
  for (const d of ['state/agents', 'state/uploads', 'state/shots/home-seat']) mkdirSync(join(data, d), { recursive: true });
  writeFileSync(join(data, 'state', 'uploads', 'other-client.png'), PNG);
  writeFileSync(join(data, 'state', 'shots', 'home-seat', 'mine.png'), PNG);
  mkdirSync(join(home, 'proj'), { recursive: true });
  writeFileSync(join(home, 'proj', 'ok.png'), PNG);
  return { home, data };
}

test('F2: a cwd equal to the data dir, or enclosing it, is not a root; a sibling or a folder below it still is', () => {
  const { home, data } = tree();
  const seat = (cwd: string) => {
    writeFileSync(join(data, 'state', 'agents', 'home-seat.json'), JSON.stringify({ cwd }));
    return agentMediaRoots(data, 'home-seat');
  };
  const own = [join(data, 'state', 'shots', 'home-seat'), join(data, 'state', 'agents', 'home-seat')];
  assert.deepEqual(seat(data), own, 'the data dir itself');
  assert.deepEqual(seat(home), own, '$HOME, which holds the data dir');
  assert.deepEqual(seat(dirname(home)), own, 'further up');
  assert.deepEqual(seat(`${data}/`), own, 'trailing slash');
  assert.deepEqual(seat(join(home, 'proj')), [join(home, 'proj'), ...own], 'a project folder beside it');
  assert.deepEqual(seat(join(data, 'work', 'x')), [join(data, 'work', 'x'), ...own], 'a folder below the data dir encloses nothing');
  // Through a symlink: an alias of $HOME is refused the same way.
  const alias = join(testTmpDir('media-f2-alias-'), 'h');
  symlinkSync(home, alias);
  assert.equal(enclosesDataDir(alias, data), true);
  assert.deepEqual(seat(alias), own);
});

test('F2 end to end: a seat started in $HOME naming another client\'s upload gets no id; its own shot does', () => {
  _resetMisses();
  const { home, data } = tree();
  writeFileSync(join(data, 'state', 'agents', 'home-seat.json'), JSON.stringify({ cwd: home }));
  const store = new MediaStore(join(data, 'state', 'media-ids.json'));
  const refs = imageRefsFor('home-seat', [
    join(data, 'state', 'uploads', 'other-client.png'),
    join(data, 'state', 'shots', 'home-seat', 'mine.png'),
    join(home, 'proj', 'ok.png'),
  ], { store, roots: (a) => agentMediaRoots(data, a) });
  assert.deepEqual(refs.map((r) => r.name), ['mine.png']);
  assert.equal(store.has('home-seat', join(data, 'state', 'uploads', 'other-client.png')), null);
});

test('F4: the miss cache is bounded: past MAX_MISSES expired keys go first, then the oldest', () => {
  _resetMisses();
  const dir = testTmpDir('media-f4-');
  const store = new MediaStore(join(dir, 'ids.json'));
  let t = 1_000_000;
  const deps = { store, roots: () => [dir], now: () => t };
  const batch = (tag: string, n: number) => Array.from({ length: n }, (_, i) => join(dir, `${tag}-${i}.png`));
  // A poll naming 3000 files that are not there: 3000 misses.
  for (const ps of chunk(batch('a', 3000), 8)) imageRefsFor('gm', ps, deps);
  assert.equal(_missCount(), 3000);
  // Later (all expired) another 3000: the expired ones are swept, the cache never passes the bound.
  t += 10_000;
  for (const ps of chunk(batch('b', 3000), 8)) imageRefsFor('gm', ps, deps);
  assert.ok(_missCount() <= MAX_MISSES, `bounded: ${_missCount()}`);
  assert.ok(_missCount() >= 3000, 'the live misses are kept');
  // Fresh misses only (none expired): still bounded, the oldest go.
  for (const ps of chunk(batch('c', 6000), 8)) imageRefsFor('gm', ps, deps);
  assert.ok(_missCount() <= MAX_MISSES, `bounded: ${_missCount()}`);
  // A miss still suppresses its re-check inside the window (the cache still works).
  const late = join(dir, 'c-5999.png');
  writeFileSync(late, PNG);
  assert.equal(imageRefsFor('gm', [late], deps).length, 0);
  t += 6_000;
  assert.equal(imageRefsFor('gm', [late], deps).length, 1);
});

function chunk<T>(xs: T[], n: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < xs.length; i += n) out.push(xs.slice(i, i + n));
  return out;
}

test('the table\'s load keeps its bound and a video row\'s kind', () => {
  const file = join(testTmpDir('media-load-'), 'ids.json');
  const s = new MediaStore(file, 3);
  const v = s.idFor('gm', '/d/copy.mp4', { kind: 'video' });
  for (const n of ['1', '2', '3', '4']) s.idFor('pm', `/p/${n}.png`);
  s.flush();
  const again = new MediaStore(file, 3);
  assert.deepEqual({ ...again.lookup(v), at: '' }, { agent: 'gm', path: '/d/copy.mp4', at: '', kind: 'video' });
  assert.equal(again.countFor('pm'), 3);
  const small = new MediaStore(file, 2);
  assert.equal(small.countFor('pm'), 2, 'a smaller cap holds on load');
});
