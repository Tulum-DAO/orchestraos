import { test } from 'node:test';
import assert from 'node:assert/strict';
import { COMPOSER_ACCEPT, newFiles, pasteAttachesFiles, attachmentMarkers } from './composerFiles.ts';
import { retryText } from './composerGate.ts';

const f = (name: string, size = 10, lastModified = 1, type = '') => ({ name, size, lastModified, type });

test('several files are kept, in the order given', () => {
  const got = newFiles([], [f('a.png'), f('b.mp4'), f('c.pdf')]);
  assert.deepEqual(got.map((x) => x.name), ['a.png', 'b.mp4', 'c.pdf']);
});

test('a second pick ADDS to what is attached instead of replacing it', () => {
  const attached = [f('a.png')];
  const got = [...attached, ...newFiles(attached, [f('b.m4a')])];
  assert.deepEqual(got.map((x) => x.name), ['a.png', 'b.m4a']);
});

test('the same file picked twice is attached once', () => {
  assert.deepEqual(newFiles([f('a.png')], [f('a.png')]), []);
  assert.equal(newFiles([], [f('a.png'), f('a.png')]).length, 1, 'duplicates within one drop collapse');
  assert.equal(newFiles([f('a.png', 10)], [f('a.png', 11)]).length, 1, 'same name, different file, is kept');
});

test('no files in, nothing out', () => {
  assert.deepEqual(newFiles([f('a')], null), []);
  assert.deepEqual(newFiles([f('a')], undefined), []);
  assert.deepEqual(newFiles([], []), []);
});

test('the picker offers video, which the upload route already accepts', () => {
  const accept = COMPOSER_ACCEPT.split(',');
  for (const ext of ['.mp4', '.mov', '.m4v', '.webm', '.png', '.pdf', '.m4a', '.mp3', '.wav']) {
    assert.ok(accept.includes(ext), `${ext} missing from the picker`);
  }
});

test('a paste attaches files only when it carries no text', () => {
  assert.equal(pasteAttachesFiles(1, ''), true, 'a copied screenshot');
  assert.equal(pasteAttachesFiles(3, ''), true, 'several copied files');
  assert.equal(pasteAttachesFiles(1, 'some copied cells'), false, 'text with a rendered image is a text paste');
  assert.equal(pasteAttachesFiles(0, ''), false);
});

test('one marker per uploaded file, images tagged IMAGE and everything else FILE', () => {
  const up = [
    { path: '/u/a.png', type: 'image/png' },
    { path: '/u/b.mp4', type: 'video/mp4' },
    { path: '/u/c.pdf', type: '' },
  ];
  assert.equal(attachmentMarkers(up, 'look'), '[IMAGE: /u/a.png] [FILE: /u/b.mp4] [FILE: /u/c.pdf] look');
  assert.equal(attachmentMarkers(up.slice(0, 1), ''), '[IMAGE: /u/a.png]', 'files with no text');
  assert.equal(attachmentMarkers([], 'just text'), 'just text');
});

test('a forced retry after an edit keeps EVERY file marker, not just the first', () => {
  const attempt = attachmentMarkers([{ path: '/u/a.png', type: 'image/png' }, { path: '/u/b.mov', type: 'video/quicktime' }], 'old');
  assert.equal(retryText(true, 'new', attempt), '[IMAGE: /u/a.png] [FILE: /u/b.mov] new');
});
