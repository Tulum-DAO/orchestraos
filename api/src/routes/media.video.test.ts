/**
 * Video in the chat through the SAME route and the same checks as images (routes/media.ts,
 * lib/mediaStore.ts openMedia, lib/mediaSniff.ts): MP4, QuickTime and WebM by their bytes, every
 * other container refused, a video cap above the image cap, and HTTP byte ranges (a <video> will
 * not seek without a 206) read from the one descriptor the checks ran on.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { existsSync, mkdirSync, openSync, closeSync, ftruncateSync, readdirSync, writeFileSync, writeSync } from 'node:fs';
import http from 'node:http';
import { join } from 'node:path';
import { testTmpDir } from '../lib/testTmp.js';
import { MAX_IMAGE_BYTES, MAX_VIDEO_BYTES, MediaStore, imageRefsFor, openMedia, resolveImageFile, sniffMedia, sniffVideo, _resetMisses } from '../lib/mediaStore.js';
import { createMediaRouter, parseRange } from './media.js';

/** A real-shaped ISO-BMFF header: a 24-byte ftyp box, then an mdat box of `body`. */
function bmff(brand: string, body: Buffer): Buffer {
  const ftyp = Buffer.alloc(24);
  ftyp.writeUInt32BE(24, 0); ftyp.write('ftyp', 4, 'latin1'); ftyp.write(brand, 8, 'latin1'); ftyp.writeUInt32BE(0x200, 12);
  ftyp.write('isomiso2', 16, 'latin1');
  const mdat = Buffer.alloc(8); mdat.writeUInt32BE(8 + body.length, 0); mdat.write('mdat', 4, 'latin1');
  return Buffer.concat([ftyp, mdat, body]);
}
// EBML header with DocType "webm" (the bytes a browser's MediaRecorder writes first).
const WEBM = Buffer.concat([Buffer.from([0x1a, 0x45, 0xdf, 0xa3, 0x9f, 0x42, 0x86, 0x81, 0x01, 0x42, 0xf7, 0x81, 0x01, 0x42, 0xf2, 0x81, 0x04, 0x42, 0xf3, 0x81, 0x08, 0x42, 0x82, 0x84]), Buffer.from('webm'), Buffer.alloc(40, 7)]);
const MKV = Buffer.concat([WEBM.subarray(0, 23), Buffer.from([0x88]), Buffer.from('matroska'), Buffer.alloc(40, 7)]);
const BODY = Buffer.from(Array.from({ length: 1000 }, (_, i) => i % 251));
const MP4 = bmff('isom', BODY);
const MOV = bmff('qt  ', BODY);

test('the video sniff: MP4 family and QuickTime by ftyp major brand, WebM by EBML DocType; everything else refused', () => {
  assert.equal(sniffVideo(MP4), 'video/mp4');
  assert.equal(sniffVideo(bmff('mp42', BODY)), 'video/mp4');
  assert.equal(sniffVideo(bmff('M4V ', BODY)), 'video/mp4');
  assert.equal(sniffVideo(MOV), 'video/quicktime');
  assert.equal(sniffVideo(WEBM), 'video/webm');
  assert.equal(sniffVideo(MKV), null, 'Matroska that is not WebM');
  assert.equal(sniffVideo(bmff('heic', BODY)), null, 'HEIC is an ftyp box but not a video');
  assert.equal(sniffVideo(bmff('M4A ', BODY)), null, 'audio-only MP4');
  assert.equal(sniffVideo(bmff('avif', BODY)), null, 'AVIF is an image');
  assert.equal(sniffMedia(bmff('avif', BODY))?.kind, 'image');
  assert.equal(sniffVideo(Buffer.from('<!doctype html><video>')), null);
  assert.equal(sniffVideo(Buffer.from([0x1a, 0x45, 0xdf, 0xa3])), null, 'EBML magic with no DocType');
  assert.equal(sniffVideo(Buffer.alloc(0)), null);
  assert.deepEqual(sniffMedia(MP4), { mime: 'video/mp4', kind: 'video' });
});

function fixture() {
  const base = testTmpDir('media-video-');
  const project = join(base, 'project');
  mkdirSync(project, { recursive: true });
  writeFileSync(join(project, 'demo.mp4'), MP4);
  writeFileSync(join(project, 'clip.mov'), MOV);
  writeFileSync(join(project, 'rec.webm'), WEBM);
  writeFileSync(join(project, 'film.mkv.mp4'), MKV);
  writeFileSync(join(project, 'page.mp4'), '<html><script>alert(1)</script></html>');
  const store = new MediaStore(join(base, 'ids.json'));
  const router = createMediaRouter({ store, roots: (a) => (a === 'gm' ? [project] : []), resolveTags: () => [] });
  return { base, project, store, router };
}

/** A sparse file of `size` bytes that starts with `head` (no real disk use). */
function sparse(path: string, head: Buffer, size: number) {
  const fd = openSync(path, 'w');
  try { writeSync(fd, head, 0, head.length, 0); ftruncateSync(fd, size); } finally { closeSync(fd); }
}

test('openMedia: a video is served as one by its bytes, under the video cap; a non-video wearing .mp4 is refused', () => {
  const f = fixture();
  const r = openMedia(join(f.project, 'demo.mp4'), [f.project]);
  assert.ok(r.ok);
  if (r.ok) { assert.equal(r.mime, 'video/mp4'); assert.equal(r.kind, 'video'); assert.equal(r.size, MP4.length); closeSync(r.fd); }
  assert.deepEqual(resolveImageFile(join(f.project, 'page.mp4'), [f.project]), { ok: false, why: 'not-an-image' });
  assert.deepEqual(resolveImageFile(join(f.project, 'film.mkv.mp4'), [f.project]), { ok: false, why: 'not-an-image' });
  // The caps are per kind: a video bigger than the image cap is fine, one over the video cap is not;
  // an image over the image cap is refused as before.
  sparse(join(f.project, 'long.mp4'), MP4.subarray(0, 64), MAX_IMAGE_BYTES + 1024);
  assert.equal(resolveImageFile(join(f.project, 'long.mp4'), [f.project]).ok, true);
  sparse(join(f.project, 'huge.mp4'), MP4.subarray(0, 64), MAX_VIDEO_BYTES + 1);
  assert.deepEqual(resolveImageFile(join(f.project, 'huge.mp4'), [f.project]), { ok: false, why: 'too-big' });
  const png = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(56)]);
  sparse(join(f.project, 'big.png'), png, MAX_IMAGE_BYTES + 1);
  assert.deepEqual(resolveImageFile(join(f.project, 'big.png'), [f.project]), { ok: false, why: 'too-big' });
});

test('stamping a video: its ref says kind "video"; an image\'s ref has no kind (older clients see the same shape)', () => {
  _resetMisses();
  const f = fixture();
  const png = join(f.project, 'shot.png');
  writeFileSync(png, Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(8)]));
  const deps = { store: f.store, roots: () => [f.project] };
  const refs = imageRefsFor('gm', [join(f.project, 'demo.mp4'), png, join(f.project, 'rec.webm'), join(f.project, 'page.mp4')], deps);
  assert.deepEqual(refs.map((r) => [r.name, r.kind]), [['demo.mp4', 'video'], ['shot.png', undefined], ['rec.webm', 'video']]);
  assert.equal('kind' in refs[1], false);
  // A held id answers from the table, kind included, without the disk.
  const again = imageRefsFor('gm', [join(f.project, 'demo.mp4')], deps);
  assert.deepEqual(again, [refs[0]]);
});

test('parseRange: one range, the suffix form, open-ended; past the end is unsatisfiable; anything else is the whole file', () => {
  assert.deepEqual(parseRange('bytes=0-9', 100), { start: 0, end: 9 });
  assert.deepEqual(parseRange('bytes=90-', 100), { start: 90, end: 99 });
  assert.deepEqual(parseRange('bytes=90-500', 100), { start: 90, end: 99 });
  assert.deepEqual(parseRange('bytes=-10', 100), { start: 90, end: 99 });
  assert.deepEqual(parseRange('bytes=-500', 100), { start: 0, end: 99 });
  assert.equal(parseRange('bytes=100-', 100), 'unsatisfiable');
  assert.equal(parseRange('bytes=-0', 100), 'unsatisfiable');
  for (const whole of [undefined, '', 'bytes=0-1,5-9', 'items=0-9', 'bytes=9-0', 'bytes=-', 'bytes=a-b', ['bytes=0-1']]) {
    assert.equal(parseRange(whole, 100), null, String(whole));
  }
});

async function withApp(router: express.Router, fn: (base: string) => Promise<void>) {
  const app = express();
  app.use('/api/media', router);
  const server = app.listen(0);
  const { port } = server.address() as AddressInfo;
  try { await fn(`http://127.0.0.1:${port}`); } finally { server.close(); }
}

test('a video by id: Accept-Ranges, a 206 for one range with its exact bytes, 416 past the end, 200 for the whole', async () => {
  const f = fixture();
  const id = f.store.idFor('gm', join(f.project, 'demo.mp4'), { kind: 'video' });
  await withApp(f.router, async (base) => {
    const whole = await fetch(`${base}/api/media/${id}`);
    assert.equal(whole.status, 200);
    assert.equal(whole.headers.get('content-type'), 'video/mp4');
    assert.equal(whole.headers.get('accept-ranges'), 'bytes');
    assert.equal(whole.headers.get('content-length'), String(MP4.length));
    assert.deepEqual(Buffer.from(await whole.arrayBuffer()), MP4);

    const part = await fetch(`${base}/api/media/${id}`, { headers: { Range: 'bytes=100-199' } });
    assert.equal(part.status, 206);
    assert.equal(part.headers.get('content-range'), `bytes 100-199/${MP4.length}`);
    assert.equal(part.headers.get('content-length'), '100');
    assert.equal(part.headers.get('content-type'), 'video/mp4');
    assert.match(part.headers.get('cache-control') || '', /^private\b/);
    assert.equal(part.headers.get('x-content-type-options'), 'nosniff');
    assert.deepEqual(Buffer.from(await part.arrayBuffer()), MP4.subarray(100, 200));

    const tail = await fetch(`${base}/api/media/${id}`, { headers: { Range: 'bytes=-16' } });
    assert.equal(tail.status, 206);
    assert.deepEqual(Buffer.from(await tail.arrayBuffer()), MP4.subarray(MP4.length - 16));

    const open = await fetch(`${base}/api/media/${id}`, { headers: { Range: 'bytes=0-' } });
    assert.equal(open.status, 206, 'the request a <video> opens with');
    assert.equal(open.headers.get('content-range'), `bytes 0-${MP4.length - 1}/${MP4.length}`);
    assert.deepEqual(Buffer.from(await open.arrayBuffer()), MP4);

    const past = await fetch(`${base}/api/media/${id}`, { headers: { Range: `bytes=${MP4.length}-` } });
    assert.equal(past.status, 416);
    assert.equal(past.headers.get('content-range'), `bytes */${MP4.length}`);
    await past.arrayBuffer();

    const multi = await fetch(`${base}/api/media/${id}`, { headers: { Range: 'bytes=0-1,4-5' } });
    assert.equal(multi.status, 200);
    assert.equal((await multi.arrayBuffer()).byteLength, MP4.length);
    const ifRange = await fetch(`${base}/api/media/${id}`, { headers: { Range: 'bytes=0-1', 'If-Range': '"x"' } });
    assert.equal(ifRange.status, 200, 'an If-Range we have no validator for gets the whole file');
    await ifRange.arrayBuffer();

    const head = await fetch(`${base}/api/media/${id}`, { method: 'HEAD', headers: { Range: 'bytes=0-9' } });
    assert.equal(head.status, 206);
    assert.equal(head.headers.get('content-length'), '10');

    // An image answers ranges the same way.
    const png = join(f.project, 'shot.png');
    writeFileSync(png, Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(40, 2)]));
    const pid = f.store.idFor('gm', png);
    const ip = await fetch(`${base}/api/media/${pid}`, { headers: { Range: 'bytes=0-7' } });
    assert.equal(ip.status, 206);
    assert.equal(ip.headers.get('content-type'), 'image/png');
    assert.equal((await ip.arrayBuffer()).byteLength, 8);

    // Many range reads in a row, some abandoned mid-body: every descriptor is released (no EMFILE, no hang).
    for (let i = 0; i < 40; i++) {
      const r = await fetch(`${base}/api/media/${id}`, { headers: { Range: `bytes=${i}-` } });
      assert.equal(r.status, 206);
      if (i % 2) await r.body?.cancel(); else await r.arrayBuffer();
    }
  });
});

test('SABOTAGE: a video id whose file became a non-video, or another container, is 404 on every range too', async () => {
  const f = fixture();
  const mov = join(f.project, 'clip.mov');
  const id = f.store.idFor('gm', mov, { kind: 'video' });
  await withApp(f.router, async (base) => {
    const ok = await fetch(`${base}/api/media/${id}`, { headers: { Range: 'bytes=0-3' } });
    assert.equal(ok.status, 206);
    assert.equal(ok.headers.get('content-type'), 'video/quicktime');
    await ok.arrayBuffer();
    writeFileSync(mov, MKV);
    for (const range of ['bytes=0-3', 'bytes=-4', undefined]) {
      const r = await fetch(`${base}/api/media/${id}`, { headers: range ? { Range: range } : {} });
      assert.equal(r.status, 404, String(range));
      await r.arrayBuffer();
    }
  });
});

// #601 review finding 5 (mutants M2, M21) and #602's (the HEAD path): every fd release was unguarded. The process's
// open descriptors are counted (/proc/self/fd) before and after N viewers that drop a stream mid-read, N answers of
// 416 and N HEAD requests, on a file far bigger than one chunk, so a stream left paused would hold its descriptor.
const fdCount = () => readdirSync('/proc/self/fd').length;

/** One request with its own socket (no keep-alive pool holding a descriptor); `drop` ends it after the first chunk. */
function rawRequest(base: string, path: string, headers: Record<string, string>, opts: { drop?: boolean; method?: string } = {}): Promise<number> {
  return new Promise((done, fail) => {
    const req = http.request(`${base}${path}`, { headers, agent: false, method: opts.method ?? 'GET' }, (res) => {
      if (opts.drop) { res.once('data', () => { req.destroy(); done(res.statusCode ?? 0); }); return; }
      res.resume();
      res.on('end', () => done(res.statusCode ?? 0));
    });
    req.on('error', (e) => (opts.drop ? undefined : fail(e)));
    req.end();
  });
}

async function settle(target: number, ms = 3000): Promise<number> {
  const until = Date.now() + ms;
  let n = fdCount();
  while (n > target && Date.now() < until) { await new Promise((r) => setTimeout(r, 25)); n = fdCount(); }
  return n;
}

test('SABOTAGE fds: a viewer dropping a stream mid-read, a 416 and a HEAD each release the file descriptor', { skip: !existsSync('/proc/self/fd') }, async () => {
  const f = fixture();
  const big = join(f.project, 'long.mp4');
  const size = 64 * 1024 * 1024;
  sparse(big, MP4.subarray(0, 64), size);
  const id = f.store.idFor('gm', big, { kind: 'video' });
  await withApp(f.router, async (base) => {
    // Warm up (lazy module loads, the listening socket), then the baseline.
    assert.equal(await rawRequest(base, `/api/media/${id}`, { Range: 'bytes=0-9' }), 206);
    const before = await settle(0, 200);
    for (let i = 0; i < 20; i++) assert.equal(await rawRequest(base, `/api/media/${id}`, { Range: `bytes=${i}-` }, { drop: true }), 206);
    const afterDrops = await settle(before);
    assert.ok(afterDrops <= before, `dropped streams released their descriptors: ${before} -> ${afterDrops}`);
    for (let i = 0; i < 20; i++) assert.equal(await rawRequest(base, `/api/media/${id}`, { Range: `bytes=${size + i}-` }), 416);
    const after416 = await settle(before);
    assert.ok(after416 <= before, `416 answers released their descriptors: ${before} -> ${after416}`);
    for (let i = 0; i < 20; i++) assert.equal(await rawRequest(base, `/api/media/${id}`, i % 2 ? { Range: 'bytes=0-9' } : {}, { method: 'HEAD' }), i % 2 ? 206 : 200);
    const afterHead = await settle(before);
    assert.ok(afterHead <= before, `HEAD answers released their descriptors: ${before} -> ${afterHead}`);
  });
});
