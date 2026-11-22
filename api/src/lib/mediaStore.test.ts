/**
 * The id table and the allow-list behind GET /api/media/:id (lib/mediaStore.ts). Every refusal the
 * route relies on is proven here on real files: outside the roots, traversal, a symlinked file, a
 * symlinked directory, a non-image wearing .png, a file over the cap.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { testTmpDir } from './testTmp.js';
import { MEDIA_ID, MediaStore, resolveImageFile, sniffImage, underRoot, agentMediaRoots } from './mediaStore.js';

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
  assert.notEqual(s.idFor('gm', '/tmp/b.png'), a);
  assert.notEqual(s.idFor('other', '/tmp/a.png'), a);
  assert.ok(!a.includes('/'), 'an id is not a path');
  // A fresh store over the same file knows the id.
  const again = new MediaStore(file);
  assert.deepEqual(again.lookup(a)?.path, '/tmp/a.png');
  assert.equal(again.lookup(a)?.agent, 'gm');
  // The table never holds anything but ids and rows.
  const raw = JSON.parse(readFileSync(file, 'utf-8'));
  for (const k of Object.keys(raw)) assert.match(k, MEDIA_ID);
});

test('lookup refuses a malformed id without reading the table', () => {
  const s = new MediaStore(join(testTmpDir('media-ids-'), 'nope.json'));
  for (const bad of ['', '../x', '/etc/passwd', 'ABCDEF01'.repeat(4), 'a'.repeat(31), 'a'.repeat(33)]) {
    assert.equal(s.lookup(bad), null, bad);
  }
});

test('a real image under a root is served with the type its bytes say', () => {
  const f = fixture();
  const r = resolveImageFile(join(f.project, 'shots', 'ok.png'), [f.data, f.project]);
  assert.ok(r.ok);
  assert.equal(r.mime, 'image/png');
  assert.equal(r.size, PNG.length);
  const j = resolveImageFile(join(f.data, 'state.jpg'), [f.data, f.project]);
  assert.ok(j.ok && j.mime === 'image/jpeg');
});

test('outside the roots, relative, and the root itself are refused', () => {
  const f = fixture();
  assert.deepEqual(resolveImageFile(join(f.outside, 'secret.png'), [f.data, f.project]), { ok: false, why: 'outside-roots' });
  assert.deepEqual(resolveImageFile('shots/ok.png', [f.project]), { ok: false, why: 'not-absolute' });
  assert.deepEqual(resolveImageFile(f.project, [f.project]), { ok: false, why: 'outside-roots' });
  // A sibling whose name merely starts with the root's.
  assert.deepEqual(resolveImageFile(`${f.project}2/x.png`, [f.project]), { ok: false, why: 'outside-roots' });
  assert.deepEqual(resolveImageFile(join(f.outside, 'secret.png'), []), { ok: false, why: 'outside-roots' });
});

test('SABOTAGE traversal: .. never walks out of a root', () => {
  const f = fixture();
  const r = resolveImageFile(`${f.project}/shots/../../outside/secret.png`, [f.data, f.project]);
  assert.deepEqual(r, { ok: false, why: 'outside-roots' });
  const r2 = resolveImageFile(`${f.project}/../outside/secret.png`, [f.project]);
  assert.deepEqual(r2, { ok: false, why: 'outside-roots' });
});

test('SABOTAGE symlink: a linked file and a linked directory inside the root are both refused', () => {
  const f = fixture();
  symlinkSync(join(f.outside, 'secret.png'), join(f.project, 'shots', 'link.png'));
  symlinkSync(f.outside, join(f.project, 'linkdir'));
  // ...and a link whose target is INSIDE the root: links are not served, full stop.
  symlinkSync(join(f.project, 'shots', 'ok.png'), join(f.project, 'inner.png'));
  assert.deepEqual(resolveImageFile(join(f.project, 'shots', 'link.png'), [f.project]), { ok: false, why: 'symlink' });
  assert.deepEqual(resolveImageFile(join(f.project, 'linkdir', 'secret.png'), [f.project]), { ok: false, why: 'symlink' });
  assert.deepEqual(resolveImageFile(join(f.project, 'inner.png'), [f.project]), { ok: false, why: 'symlink' });
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

test('an agent\'s roots: the data dir, plus its cwd when the state file names one', () => {
  const data = testTmpDir('media-roots-');
  mkdirSync(join(data, 'state', 'agents'), { recursive: true });
  writeFileSync(join(data, 'state', 'agents', 'gm.json'), JSON.stringify({ cwd: '/srv/work/gm' }));
  assert.deepEqual(agentMediaRoots(data, 'gm'), [data, '/srv/work/gm']);
  assert.deepEqual(agentMediaRoots(data, 'unknown-seat'), [data]);
  // A path-shaped id builds no path.
  assert.deepEqual(agentMediaRoots(data, '../../etc'), [data]);
});
