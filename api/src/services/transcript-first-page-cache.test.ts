/**
 * The finished first page per seat: a hit returns the very same entry while the key is valid; a
 * new validity string (the log grew, a queued row landed) builds again; the table is bounded, the
 * least recently used page leaving first.
 *
 * Run: npx tsx --test src/services/transcript-first-page-cache.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { gunzipSync } from 'node:zlib';
import { writeFileSync, renameSync, statSync, utimesSync } from 'node:fs';
import { join } from 'node:path';
import { testTmpDir } from '../lib/testTmp.js';
import { firstPage, firstPageCacheStats, clearFirstPageCache, FIRST_PAGE_CACHE_MAX } from './transcript-first-page-cache.js';

test('a hit is the same entry; a changed validity builds again', () => {
  clearFirstPageCache();
  let builds = 0;
  const build = () => { builds += 1; return { body: { n: builds, pad: 'y'.repeat(2048) } }; };
  const a = firstPage('seat-a', 40, 'ino1:100:1', build);
  const b = firstPage('seat-a', 40, 'ino1:100:1', build);
  assert.equal(builds, 1);
  assert.equal(a, b, 'the same entry object');
  assert.equal(JSON.parse(a.json).n, 1);
  assert.ok(a.etag.startsWith('W/"'));
  assert.ok(a.gzip && a.gzip.length < a.json.length, 'the body is kept gzipped too');
  assert.equal(gunzipSync(a.gzip!).toString('utf-8'), a.json);
  const c = firstPage('seat-a', 40, 'ino1:160:2', build);
  assert.equal(builds, 2, 'the log changed: built again');
  assert.equal(JSON.parse(c.json).n, 2);
  // Another limit is another page of the same seat.
  firstPage('seat-a', 150, 'ino1:160:2', build);
  assert.equal(builds, 3);
  assert.deepEqual(firstPageCacheStats(), { size: 2, hits: 1, misses: 3 });
});

test('a small body is not gzipped (not worth the header)', () => {
  clearFirstPageCache();
  const e = firstPage('seat-s', 40, 'v', () => ({ body: { tiny: true } }));
  assert.equal(e.gzip, null);
});

test('the table is bounded; the least recently used page leaves first', () => {
  clearFirstPageCache();
  for (let i = 0; i < FIRST_PAGE_CACHE_MAX; i++) firstPage(`seat-${i}`, 40, 'v', () => ({ body: { i } }));
  assert.equal(firstPageCacheStats().size, FIRST_PAGE_CACHE_MAX);
  firstPage('seat-0', 40, 'v', () => ({ body: { i: -1 } }));           // touch the oldest: it is now the newest
  firstPage('seat-new', 40, 'v', () => ({ body: { i: 'new' } }));      // one over the bound
  assert.equal(firstPageCacheStats().size, FIRST_PAGE_CACHE_MAX);
  let built = false;
  firstPage('seat-0', 40, 'v', () => { built = true; return { body: {} }; });
  assert.equal(built, false, 'the touched page stayed');
  firstPage('seat-1', 40, 'v', () => { built = true; return { body: {} }; });
  assert.equal(built, true, 'the least recently used page was dropped');
});

test('a file the page read (an earlier generation) rewritten, removed or back again builds the page anew', () => {
  clearFirstPageCache();
  const dir = testTmpDir('first-page-deps-');
  const past = join(dir, 'past.jsonl');
  writeFileSync(past, 'one\n');
  let builds = 0;
  const build = () => { builds += 1; return { body: { n: builds }, deps: [past] }; };
  firstPage('seat-d', 40, 'v', build);
  firstPage('seat-d', 40, 'v', build);
  assert.equal(builds, 1, 'unchanged: a hit');
  writeFileSync(past, 'one\ntwo\n');
  firstPage('seat-d', 40, 'v', build);
  assert.equal(builds, 2, 'rewritten: built again');
  renameSync(past, `${past}.aside`);
  firstPage('seat-d', 40, 'v', build);
  assert.equal(builds, 3, 'gone: built again');
  firstPage('seat-d', 40, 'v', build);
  assert.equal(builds, 3, 'still gone: a hit');
  renameSync(`${past}.aside`, past);
  firstPage('seat-d', 40, 'v', build);
  assert.equal(builds, 4, 'back: built again');
});

test('a file the page read replaced atomically by one of the same size and mtime (a new inode) builds the page anew', () => {
  clearFirstPageCache();
  const dir = testTmpDir('first-page-deps-ino-');
  const past = join(dir, 'past.jsonl');
  writeFileSync(past, 'question 196\n');
  const pinned = new Date(Math.floor(Date.now() / 1000) * 1000);  // utimes keeps whole ms: pin a whole-second mtime
  utimesSync(past, pinned, pinned);
  const old = statSync(past);
  let builds = 0;
  const build = () => { builds += 1; return { body: { n: builds }, deps: [past] }; };
  firstPage('seat-i', 40, 'v', build);
  firstPage('seat-i', 40, 'v', build);
  assert.equal(builds, 1);
  const tmp = join(dir, 'past.jsonl.tmp');
  writeFileSync(tmp, 'questiYn 196\n');                    // same length, different bytes
  utimesSync(tmp, pinned, pinned);                            // same mtime
  renameSync(tmp, past);                                      // atomic replace: a new inode
  const now = statSync(past);
  assert.equal(now.size, old.size); assert.equal(now.mtimeMs, old.mtimeMs); assert.notEqual(now.ino, old.ino, 'precondition: only the inode moved');
  firstPage('seat-i', 40, 'v', build);
  assert.equal(builds, 2, 'the replaced file is seen: built again');
});
