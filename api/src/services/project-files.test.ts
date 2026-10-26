import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync } from 'fs';
import { tmpdir } from 'os';
import path from 'path';
import { listProjectFiles, resolveInside, readProjectFile, unfiled } from './project-files.js';

function tree() {
  const root = mkdtempSync(path.join(tmpdir(), 'proj-'));
  const folder = path.join(root, 'acme');
  mkdirSync(path.join(folder, 'site', 'img'), { recursive: true });
  mkdirSync(path.join(folder, 'node_modules', 'x'), { recursive: true });
  mkdirSync(path.join(folder, '.git'), { recursive: true });
  writeFileSync(path.join(folder, 'README.md'), '# Acme\n');
  writeFileSync(path.join(folder, 'site', 'index.html'), '<h1>hi</h1>');
  writeFileSync(path.join(folder, 'site', 'img', 'logo.png'), Buffer.from([0x89, 0x50, 0x4e, 0x47, 0]));
  writeFileSync(path.join(folder, 'session.jsonl'), '{}\n');
  writeFileSync(path.join(folder, 'node_modules', 'x', 'a.js'), 'x');
  writeFileSync(path.join(folder, '.git', 'HEAD'), 'ref');
  writeFileSync(path.join(folder, 'blob.bin'), Buffer.from([1, 0, 2]));
  writeFileSync(path.join(root, 'outside.txt'), 'secret');
  symlinkSync(path.join(root, 'outside.txt'), path.join(folder, 'link.txt'));
  return { root, folder };
}

test('every project file is listed except .jsonl logs and tool directories', () => {
  const { folder } = tree();
  const names = listProjectFiles(folder).files.map((f) => f.path);
  assert.deepEqual(names, ['blob.bin', 'README.md', 'site/img/logo.png', 'site/index.html'].sort((a, b) => a.localeCompare(b)));
});

test('nothing outside the project folder can be read', () => {
  const { folder } = tree();
  assert.ok(resolveInside(folder, 'site/index.html'));
  for (const bad of ['../outside.txt', '/etc/passwd', 'link.txt', 'session.jsonl', 'node_modules/x/a.js', '.git/HEAD', '', 'site/../../outside.txt']) {
    assert.equal(resolveInside(folder, bad), null, bad);
  }
});

test('text is shown, images and binaries are said to be what they are', () => {
  const { folder } = tree();
  const text = readProjectFile(resolveInside(folder, 'README.md')!, 'README.md');
  assert.equal(text.kind, 'text');
  assert.equal(text.kind === 'text' && text.text, '# Acme\n');
  assert.equal(readProjectFile(resolveInside(folder, 'site/img/logo.png')!, 'site/img/logo.png').kind, 'image');
  assert.equal(readProjectFile(resolveInside(folder, 'blob.bin')!, 'blob.bin').kind, 'binary');
});

test('files outside the project layout are unfiled', () => {
  assert.deepEqual(unfiled(['PROJECT.md', 'deliverables/a.md', 'stray.txt', 'misc/b.png', 'site/index.html']), ['stray.txt', 'misc/b.png']);
});
