/**
 * The image detector (lib/imageRefs.ts): only absolute image paths the agent wrote, only the
 * target of a tool that writes a file, never anything guessed.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { findImagePaths, imageName, imagePathsInToolUse, MAX_IMAGES_PER_ITEM } from './imageRefs.js';

test('absolute image paths in a reply, in order, once each', () => {
  const text = 'Saved the chart to /tmp/out/chart.png and the photo to `/srv/data/pics/cat.JPG`.\n'
    + 'Again: /tmp/out/chart.png (same file). Also (/home/u/shots/a-b_c.webp) and "/x/y.gif".';
  assert.deepEqual(findImagePaths(text), ['/tmp/out/chart.png', '/srv/data/pics/cat.JPG', '/home/u/shots/a-b_c.webp', '/x/y.gif']);
});

test('relative paths, URLs, svg, and non-image files are not images', () => {
  assert.deepEqual(findImagePaths('see out/chart.png and ./a.png and ~/b.png'), []);
  assert.deepEqual(findImagePaths('https://example.invalid/img/a.png'), []);
  assert.deepEqual(findImagePaths('/tmp/logo.svg and /tmp/notes.md and /tmp/archive.png.zip'), []);
  assert.deepEqual(findImagePaths(''), []);
  assert.deepEqual(findImagePaths('no slash here'), []);
});

test('traversal and doubled separators are not paths an image lives at', () => {
  assert.deepEqual(findImagePaths('/tmp/../etc/x.png'), []);
  assert.deepEqual(findImagePaths('/tmp//x.png'), []);
  assert.deepEqual(findImagePaths('/./x.png'), []);
});

test('a path ends where the sentence does', () => {
  assert.deepEqual(findImagePaths('Written to /tmp/a.png.'), ['/tmp/a.png']);
  assert.deepEqual(findImagePaths('Written to /tmp/a.png, then /tmp/b.jpeg;'), ['/tmp/a.png', '/tmp/b.jpeg']);
  assert.deepEqual(findImagePaths('<img src="/tmp/a.png">'), ['/tmp/a.png']);
});

test('at most MAX_IMAGES_PER_ITEM per item', () => {
  const text = Array.from({ length: MAX_IMAGES_PER_ITEM + 5 }, (_, i) => `/tmp/f${i}.png`).join(' ');
  assert.equal(findImagePaths(text).length, MAX_IMAGES_PER_ITEM);
});

test('a Write to an image path is an image the agent made; a Read or an Edit is not', () => {
  assert.deepEqual(imagePathsInToolUse('Write', { file_path: '/tmp/out/chart.png', content: '...' }), ['/tmp/out/chart.png']);
  assert.deepEqual(imagePathsInToolUse('write_file', { path: '/tmp/a.webp' }), ['/tmp/a.webp']);
  assert.deepEqual(imagePathsInToolUse('create_file', { TargetFile: '/tmp/a.jpg' }), ['/tmp/a.jpg']);
  assert.deepEqual(imagePathsInToolUse('Read', { file_path: '/tmp/out/chart.png' }), []);
  assert.deepEqual(imagePathsInToolUse('Edit', { file_path: '/tmp/out/chart.png' }), []);
  assert.deepEqual(imagePathsInToolUse('Write', { file_path: '/tmp/notes.md' }), []);
  assert.deepEqual(imagePathsInToolUse('Write', { file_path: 'relative.png' }), []);
  assert.deepEqual(imagePathsInToolUse('Write', null), []);
});

test('the name is the basename, never a path', () => {
  assert.equal(imageName('/tmp/out/chart.png'), 'chart.png');
});
