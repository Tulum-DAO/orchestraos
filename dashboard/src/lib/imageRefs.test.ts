/**
 * Images in the chat, the client half (lib/imageRefs.ts): a thumbnail's URL is an opaque id and
 * nothing else; the detector matches the API's; stamped refs pass through, bad ones are dropped.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { findImagePaths, imageName, mediaUrl, stampedImages, unresolvedImages, MAX_IMAGES_PER_ITEM, THUMB } from './imageRefs';

const ID = 'ab'.repeat(16);

test('the URL carries the id and nothing else; anything but an id makes no URL', () => {
  assert.equal(mediaUrl(ID), `/api/media/${ID}`);
  assert.equal(mediaUrl(ID, true), `/api/media/${ID}?download`);
  for (const bad of ['', '/tmp/a.png', '../etc', ID.toUpperCase(), ID.slice(1), `${ID}/x`]) assert.equal(mediaUrl(bad), null, bad);
});

test('the detector matches the API\'s: absolute image paths, once, where a sentence ends', () => {
  assert.deepEqual(findImagePaths('Saved /tmp/out/chart.png. Also `/srv/p/cat.JPG`, (/x/y.webp) and /tmp/out/chart.png again'),
    ['/tmp/out/chart.png', '/srv/p/cat.JPG', '/x/y.webp']);
  assert.deepEqual(findImagePaths('out/chart.png ~/a.png https://h/a.png /tmp/logo.svg /tmp/a.png.zip /tmp/../x.png'), []);
  assert.equal(findImagePaths(Array.from({ length: 20 }, (_, i) => `/t/${i}.png`).join(' ')).length, MAX_IMAGES_PER_ITEM);
});

test('unresolved images (Arturo) have a path and a name and no id yet', () => {
  assert.deepEqual(unresolvedImages('see /tmp/shots/a.png'), [{ id: '', path: '/tmp/shots/a.png', name: 'a.png' }]);
  assert.deepEqual(unresolvedImages('nothing here'), []);
  assert.equal(imageName('/a/b/c.png'), 'c.png');
});

test('stamped refs pass through; a ref without a real id is dropped; none is undefined', () => {
  assert.deepEqual(stampedImages([{ id: ID, path: '/p/a.png', name: 'a.png' }]), [{ id: ID, path: '/p/a.png', name: 'a.png' }]);
  assert.deepEqual(stampedImages([{ id: ID, path: '/p/a.png', name: '' }]), [{ id: ID, path: '/p/a.png', name: 'a.png' }]);
  assert.equal(stampedImages([{ id: '/p/a.png', path: '/p/a.png', name: 'a.png' }]), undefined);
  assert.equal(stampedImages([]), undefined);
  assert.equal(stampedImages(undefined), undefined);
});

test('the thumbnail box is fixed, so the thread never shifts while bytes arrive', () => {
  assert.ok(THUMB.width > 0 && THUMB.height > 0);
});
