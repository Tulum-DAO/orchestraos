/**
 * The id table and the allow-list behind GET /api/media/:id (lib/mediaStore.ts). Every refusal the
 * route relies on is proven here on real files: outside the roots, traversal, a symlinked file, a
 * symlinked directory, a hard link, a non-image wearing .png, a file over the cap, and the swap of
 * a validated file for a link before the open (the open refuses it: there is no second lookup).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { closeSync, existsSync, linkSync, mkdirSync, readFileSync, readSync, renameSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { testTmpDir } from './testTmp.js';
import {
  MEDIA_ID, MediaStore, agentMediaRoots, imageRefsFor, itemTime, openImage, resolveImageFile, sniffImage, underRoot, _resetMisses,
} from './mediaStore.js';

const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(64, 1)]);
const JPEG = Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), Buffer.alloc(32, 0)]);

function fixture() {
  const base = testTmpDir('media-');
  const data = join(base, 'data');
  const project = join(base, 'project');
  const outside = join(base, 'outside');
  for (const d of [data, project, outside, join(project, 'shots')]) mkdirSync(d, { recursive: true });
  writeFileSync(join(project, 'shots', 'ok.png'), PNG);
  writeFileSync(join(data, 'state.jpg'), JPEG);
  writeFileSync(join(outside, 'secret.png'), PNG);
  writeFileSync(join(project, 'fake.png'), '<svg onload="alert(1)"></svg>');
  writeFileSync(join(project, 'text.png'), 'hello');
  return { base, data, project, outside };
}

test('ids are opaque, stable per (agent, path), distinct across paths and agents, and persisted', () => {
  const dir = testTmpDir('media-ids-');
  const file = join(dir, 'state', 'media-ids.json');
  const s = new MediaStore(file);
  const a = s.idFor('gm', '/tmp/a.png');
  assert.match(a, MEDIA_ID);
  assert.equal(s.idFor('gm', '/tmp/a.png'), a);
  assert.equal(s.has('gm', '/tmp/a.png'), a);
  assert.equal(s.has('gm', '/tmp/zzz.png'), null);
  assert.notEqual(s.idFor('gm', '/tmp/b.png'), a);
  assert.notEqual(s.idFor('other', '/tmp/a.png'), a);
  assert.ok(!a.includes('/'), 'an id is not a path');
  // Minting does not write synchronously: the file appears after the save delay, or on flush.
  assert.equal(existsSync(file), false);
  s.flush();
  const again = new MediaStore(file);
  assert.deepEqual(again.lookup(a)?.path, '/tmp/a.png');
  assert.equal(again.lookup(a)?.agent, 'gm');
  const raw = JSON.parse(readFileSync(file, 'utf-8'));
  for (const k of Object.keys(raw)) assert.match(k, MEDIA_ID);
});

test('the table is bounded per agent: the oldest row goes, other agents keep theirs', () => {
  const s = new MediaStore(join(testTmpDir('media-cap-'), 'ids.json'), 3);
  const first = s.idFor('gm', '/p/1.png');
  s.idFor('gm', '/p/2.png'); s.idFor('gm', '/p/3.png');
  const other = s.idFor('pm', '/p/1.png');
  s.idFor('gm', '/p/4.png');
  assert.equal(s.countFor('gm'), 3);
  assert.equal(s.lookup(first), null, 'the oldest row was dropped');
  assert.equal(s.has('gm', '/p/1.png'), null);
  assert.ok(s.lookup(other), 'another agent\'s row is untouched');
  assert.ok(s.has('gm', '/p/4.png'));
});

test('lookup refuses a malformed id without reading the table', () => {
  const s = new MediaStore(join(testTmpDir('media-ids-'), 'nope.json'));
  for (const bad of ['', '../x', '/etc/passwd', 'ABCDEF01'.repeat(4), 'a'.repeat(31), 'a'.repeat(33)]) {
    assert.equal(s.lookup(bad), null, bad);
  }
});

test('a real image under a root is served with the type its bytes say, from one open descriptor', () => {
  const f = fixture();
  const r = openImage(join(f.project, 'shots', 'ok.png'), [f.data, f.project]);
  assert.ok(r.ok);
  assert.equal(r.mime, 'image/png');
  assert.equal(r.size, PNG.length);
  const buf = Buffer.alloc(PNG.length);
  assert.equal(readSync(r.fd, buf, 0, PNG.length, 0), PNG.length);
  assert.deepEqual(buf, PNG);
  closeSync(r.fd);
  const j = resolveImageFile(join(f.data, 'state.jpg'), [f.data, f.project]);
  assert.ok(j.ok && j.mime === 'image/jpeg');
});

test('outside the roots, relative, and the root itself are refused', () => {
  const f = fixture();
  assert.deepEqual(resolveImageFile(join(f.outside, 'secret.png'), [f.data, f.project]), { ok: false, why: 'outside-roots' });
  assert.deepEqual(resolveImageFile('shots/ok.png', [f.project]), { ok: false, why: 'not-absolute' });
  assert.deepEqual(resolveImageFile(f.project, [f.project]), { ok: false, why: 'outside-roots' });
  assert.deepEqual(resolveImageFile(`${f.project}2/x.png`, [f.project]), { ok: false, why: 'outside-roots' });
  assert.deepEqual(resolveImageFile(join(f.outside, 'secret.png'), []), { ok: false, why: 'outside-roots' });
});

test('SABOTAGE traversal: .. never walks out of a root', () => {
  const f = fixture();
  assert.deepEqual(resolveImageFile(`${f.project}/shots/../../outside/secret.png`, [f.data, f.project]), { ok: false, why: 'outside-roots' });
  assert.deepEqual(resolveImageFile(`${f.project}/../outside/secret.png`, [f.project]), { ok: false, why: 'outside-roots' });
});

test('SABOTAGE symlink: a linked file and a linked directory inside the root are both refused', () => {
  const f = fixture();
  symlinkSync(join(f.outside, 'secret.png'), join(f.project, 'shots', 'link.png'));
  symlinkSync(f.outside, join(f.project, 'linkdir'));
  symlinkSync(join(f.project, 'shots', 'ok.png'), join(f.project, 'inner.png'));
  assert.deepEqual(resolveImageFile(join(f.project, 'shots', 'link.png'), [f.project]), { ok: false, why: 'symlink' });
  assert.deepEqual(resolveImageFile(join(f.project, 'linkdir', 'secret.png'), [f.project]), { ok: false, why: 'symlink' });
  assert.deepEqual(resolveImageFile(join(f.project, 'inner.png'), [f.project]), { ok: false, why: 'symlink' });
});

test('SABOTAGE hard link: a second name for a file outside the root is refused', () => {
  const f = fixture();
  try { linkSync(join(f.outside, 'secret.png'), join(f.project, 'hard.png')); }
  catch { return; }   // a filesystem that refuses the link cannot carry the attack
  assert.deepEqual(resolveImageFile(join(f.project, 'hard.png'), [f.project]), { ok: false, why: 'hardlink' });
});

test('SABOTAGE TOCTOU: a file validated a moment ago, swapped for a link, is refused at the open', () => {
  const f = fixture();
  const target = join(f.project, 'shots', 'ok.png');
  assert.ok(resolveImageFile(target, [f.project]).ok, 'validated');
  // The swap, as an agent writing in its own project dir could do between validation and read.
  renameSync(target, join(f.project, 'shots', 'moved.png'));
  symlinkSync(join(f.outside, 'secret.png'), target);
  const r = openImage(target, [f.project]);
  assert.deepEqual(r, { ok: false, why: 'symlink' });
});

test('a root reached through a symlink still serves its own files (the link is above the root, not below)', () => {
  const f = fixture();
  const alias = join(f.base, 'alias');
  symlinkSync(f.project, alias);
  const r = resolveImageFile(join(alias, 'shots', 'ok.png'), [alias]);
  assert.ok(r.ok, JSON.stringify(r));
});

test('content, not extension: a .png that is not an image is refused; a missing file is not found', () => {
  const f = fixture();
  assert.deepEqual(resolveImageFile(join(f.project, 'fake.png'), [f.project]), { ok: false, why: 'not-an-image' });
  assert.deepEqual(resolveImageFile(join(f.project, 'text.png'), [f.project]), { ok: false, why: 'not-an-image' });
  assert.deepEqual(resolveImageFile(join(f.project, 'gone.png'), [f.project]), { ok: false, why: 'not-a-file' });
  assert.deepEqual(resolveImageFile(join(f.project, 'shots'), [f.project]), { ok: false, why: 'not-a-file' });
});

test('a file over the size cap is refused', () => {
  const f = fixture();
  assert.deepEqual(resolveImageFile(join(f.project, 'shots', 'ok.png'), [f.project], 16), { ok: false, why: 'too-big' });
});

test('the sniff table: the served types by magic bytes, nothing else', () => {
  assert.equal(sniffImage(PNG), 'image/png');
  assert.equal(sniffImage(JPEG), 'image/jpeg');
  assert.equal(sniffImage(Buffer.from('GIF89a......')), 'image/gif');
  assert.equal(sniffImage(Buffer.concat([Buffer.from('RIFF'), Buffer.alloc(4), Buffer.from('WEBP')])), 'image/webp');
  assert.equal(sniffImage(Buffer.from('BM......')), 'image/bmp');
  assert.equal(sniffImage(Buffer.concat([Buffer.alloc(4), Buffer.from('ftypavif')])), 'image/avif');
  assert.equal(sniffImage(Buffer.from('<svg xmlns="http://www.w3.org/2000/svg">')), null);
  assert.equal(sniffImage(Buffer.from('<!doctype html>')), null);
  assert.equal(sniffImage(Buffer.alloc(0)), null);
  assert.equal(sniffImage(Buffer.from([0x89, 0x50])), null);
});

test('underRoot is a string rule: under, never equal, never a sibling prefix', () => {
  assert.equal(underRoot('/a/b/c.png', '/a/b'), true);
  assert.equal(underRoot('/a/b', '/a/b'), false);
  assert.equal(underRoot('/a/bc/x.png', '/a/b'), false);
  assert.equal(underRoot('/a/b/../c/x.png', '/a/b'), false);
  assert.equal(underRoot('relative.png', '/a/b'), false);
});

test('an agent\'s roots: its cwd and its own folders under the data dir; never the data dir itself', () => {
  const data = testTmpDir('media-roots-');
  mkdirSync(join(data, 'state', 'agents'), { recursive: true });
  writeFileSync(join(data, 'state', 'agents', 'gm.json'), JSON.stringify({ cwd: '/srv/work/gm' }));
  assert.deepEqual(agentMediaRoots(data, 'gm'), ['/srv/work/gm', join(data, 'state', 'shots', 'gm'), join(data, 'state', 'agents', 'gm')]);
  assert.deepEqual(agentMediaRoots(data, 'unknown-seat'), [join(data, 'state', 'shots', 'unknown-seat'), join(data, 'state', 'agents', 'unknown-seat')]);
  assert.deepEqual(agentMediaRoots(data, '../../etc'), []);
  for (const r of agentMediaRoots(data, 'gm')) assert.notEqual(r, data);
});

test('cross-client on a real-shaped data dir: an agent naming another client\'s upload, or anything under state/uploads, gets no id', () => {
  const data = testTmpDir('media-tree-');
  for (const d of ['state/agents', 'state/uploads', 'state/shots/acme-dev', 'state/shots/globex-dev', 'state/clients/globex']) mkdirSync(join(data, d), { recursive: true });
  writeFileSync(join(data, 'state', 'agents', 'acme-dev.json'), JSON.stringify({ cwd: join(data, 'work', 'acme') }));
  mkdirSync(join(data, 'work', 'acme'), { recursive: true });
  writeFileSync(join(data, 'work', 'acme', 'mine.png'), PNG);
  writeFileSync(join(data, 'state', 'shots', 'acme-dev', 'shot.png'), PNG);
  writeFileSync(join(data, 'state', 'shots', 'globex-dev', 'theirs.png'), PNG);
  writeFileSync(join(data, 'state', 'uploads', '1792661089_gkjjha.png'), PNG);
  writeFileSync(join(data, 'state', 'clients', 'globex', 'brand.png'), PNG);
  const store = new MediaStore(join(data, 'state', 'media-ids.json'));
  const deps = { store, roots: (a: string) => agentMediaRoots(data, a) };
  const refs = imageRefsFor('acme-dev', [
    join(data, 'work', 'acme', 'mine.png'),
    join(data, 'state', 'shots', 'acme-dev', 'shot.png'),
    join(data, 'state', 'shots', 'globex-dev', 'theirs.png'),
    join(data, 'state', 'uploads', '1792661089_gkjjha.png'),
    join(data, 'state', 'clients', 'globex', 'brand.png'),
    join(data, 'state', 'agents', 'acme-dev.json'),
  ], deps);
  assert.deepEqual(refs.map((r) => r.name), ['mine.png', 'shot.png']);
  for (const r of refs) assert.match(r.id, MEDIA_ID);
  // And a row is never minted for the refused ones: nothing to serve later either.
  assert.equal(store.has('acme-dev', join(data, 'state', 'uploads', '1792661089_gkjjha.png')), null);
  assert.equal(store.has('acme-dev', join(data, 'state', 'shots', 'globex-dev', 'theirs.png')), null);
});

test('stamping mints only for a file that exists and passes; a miss is re-checked later, a hit never touches the disk again', () => {
  _resetMisses();
  const f = fixture();
  const store = new MediaStore(join(f.base, 'ids.json'));
  let t = 1_000_000;
  const deps = { store, roots: () => [f.project], now: () => t };
  const ok = join(f.project, 'shots', 'ok.png');
  const later = join(f.project, 'shots', 'later.png');
  assert.deepEqual(imageRefsFor('gm', [ok, later, join(f.project, 'fake.png'), join(f.outside, 'secret.png')], deps).map((r) => r.path), [ok]);
  assert.equal(store.countFor('gm'), 1);
  // The file arrives: inside the miss window nothing changes, after it the path is stamped.
  writeFileSync(later, PNG);
  assert.equal(imageRefsFor('gm', [later], deps).length, 0);
  t += 6_000;
  assert.equal(imageRefsFor('gm', [later], deps).length, 1);
  // A minted path is answered from the table even once the file is gone (the route refuses it then).
  renameSync(ok, join(f.project, 'shots', 'gone-away.png'));
  assert.equal(imageRefsFor('gm', [ok], deps)[0]?.id, store.has('gm', ok));
});

// #601 delta review finding 1: "at the cap the row referenced longest ago goes" had no test outside a full window.
test('SABOTAGE evict order: at the cap the row referenced longest ago goes, never the one referenced last', () => {
  const s = new MediaStore(join(testTmpDir('media-evict-'), 'ids.json'), 5);
  const T = Date.parse('2026-11-22T00:00:00Z');
  const ids = [1, 2, 3, 4, 5].map((i) => s.mint('gm', `/p/a${i}.png`, { refAt: T + i })!);
  // a5 is read a few hundred times (the route serving it): a use never makes an older reference newer.
  for (let i = 0; i < 300; i++) s.lookup(ids[4]);
  // A limit/poll window naming only a6: every held row is unprotected, and only a1 may go.
  const a6 = s.mint('gm', '/p/a6.png', { refAt: T + 6, protect: new Set(['/p/a6.png']) });
  assert.ok(a6);
  assert.equal(s.countFor('gm'), 5);
  assert.equal(s.has('gm', '/p/a1.png'), null, 'a1, referenced longest ago, went');
  for (let i = 2; i <= 5; i++) assert.equal(s.has('gm', `/p/a${i}.png`), ids[i - 1], `a${i} stays`);
  // Then a2, a3 ... in that order, whatever was looked up in between.
  s.lookup(ids[1]);
  assert.ok(s.mint('gm', '/p/a7.png', { refAt: T + 7 }));
  assert.equal(s.has('gm', '/p/a2.png'), null);
  assert.equal(s.has('gm', '/p/a3.png'), ids[2]);
});

test('SABOTAGE evict order: "last referenced" never moves backwards, so an older page naming a row again does not rewind it', () => {
  const s = new MediaStore(join(testTmpDir('media-rewind-'), 'ids.json'), 3);
  const T = Date.parse('2026-11-22T00:00:00Z');
  const H = 3_600_000;
  const [c1, c2, c3] = [1, 2, 3].map((i) => s.mint('gm', `/p/c${i}.png`, { refAt: T + 3 * H + i })!);
  // An older page (T+1h) names c3 again: its row keeps the newer time.
  assert.equal(s.has('gm', '/p/c3.png', T + H), c3);
  assert.equal(s.lookup(c3)?.seen, T + 3 * H + 3);
  // c4 arrives at the live end: c1 goes, c3 stays.
  assert.ok(s.mint('gm', '/p/c4.png', { refAt: T + 4 * H }));
  assert.equal(s.has('gm', '/p/c1.png'), null, 'c1 went');
  assert.equal(s.has('gm', '/p/c3.png'), c3, 'c3 kept its id');
  // And a newer reference does move a row on: c2, the oldest left (T+3h), referenced again at T+5h, so at the
  // next mint c3 goes and c2 stays.
  assert.equal(s.has('gm', '/p/c2.png', T + 5 * H), c2);
  assert.ok(s.mint('gm', '/p/c5.png', { refAt: T + 6 * H }));
  assert.equal(s.has('gm', '/p/c3.png'), null, 'c3 went');
  assert.equal(s.has('gm', '/p/c2.png'), c2, 'c2, moved on, stays');
  assert.equal(s.lookup(c2)?.seen, T + 5 * H);
});

test('SABOTAGE evict order: the eviction order survives a save and load, and a row with no time counts as oldest', () => {
  const dir = testTmpDir('media-reload-');
  const file = join(dir, 'ids.json');
  const s = new MediaStore(file, 3);
  const T = Date.parse('2026-11-22T00:00:00Z');
  const none = s.mint('gm', '/p/none.png')!;
  const old = s.mint('gm', '/p/old.png', { refAt: T })!;
  const young = s.mint('gm', '/p/young.png', { refAt: T + 2 })!;
  s.lookup(none);   // used last: still the oldest by reference
  s.flush();
  const r = new MediaStore(file, 3);
  assert.ok(r.mint('gm', '/p/x.png', { refAt: T + 3 }));
  assert.equal(r.lookup(none), null, 'the row with no time went first');
  assert.ok(r.mint('gm', '/p/y.png', { refAt: T + 4 }));
  assert.equal(r.lookup(old), null);
  assert.ok(r.lookup(young));
});

test('itemTime: ISO strings and ms pass through, seconds (Arturo\'s turns) become ms, anything else is undefined', () => {
  assert.equal(itemTime('2026-11-22T00:00:00Z'), Date.parse('2026-11-22T00:00:00Z'));
  assert.equal(itemTime(1_763_769_600_000), 1_763_769_600_000);
  assert.equal(itemTime(1_763_769_600), 1_763_769_600_000);
  assert.equal(itemTime(1_763_769_600.5), 1_763_769_600_500);
  assert.equal(itemTime(0), 0);
  assert.equal(itemTime(''), undefined);
  assert.equal(itemTime('yesterday'), undefined);
  assert.equal(itemTime(NaN), undefined);
  assert.equal(itemTime(null), undefined);
  assert.equal(itemTime(undefined), undefined);
});
