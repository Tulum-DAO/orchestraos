/**
 * Share in the chat's full-screen view (lib/mediaShare.ts): the FILE through the Web Share API when the
 * browser can share files, else a download; never the link (it needs this install's sign-in), never a
 * credential in a URL.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { canShareFiles, mediaTypeFor, shareMedia, SHARE_FALLBACK_TITLE, type ShareNav } from './mediaShare';

const URL_ = '/api/media/' + 'ab'.repeat(16);

function nav(opts: { files?: boolean; reject?: string } = {}) {
  const shared: ShareData[] = [];
  const n: ShareNav = {
    canShare: (d?: ShareData) => !!opts.files && !!d?.files?.length,
    share: async (d: ShareData) => { if (opts.reject) { const e = new Error('x'); e.name = opts.reject; throw e; } shared.push(d); },
  };
  return { n, shared };
}

function deps(n: ShareNav | undefined, fetched: string[], downloads: number[]) {
  return {
    nav: n,
    fetchBlob: async (u: string) => { fetched.push(u); return new Blob([new Uint8Array([1, 2, 3])], { type: 'video/mp4' }); },
    download: () => { downloads.push(1); },
  };
}

test('a browser that can share files gets the FILE (fetched from the route), never a URL', async () => {
  const { n, shared } = nav({ files: true });
  const fetched: string[] = []; const downloads: number[] = [];
  assert.equal(canShareFiles(n, 'video/mp4'), true);
  assert.equal(await shareMedia(URL_, 'demo.mp4', 'video/mp4', deps(n, fetched, downloads)), 'shared');
  assert.deepEqual(fetched, [URL_]);
  assert.equal(downloads.length, 0);
  assert.equal(shared.length, 1);
  const data = shared[0];
  assert.equal(data.url, undefined, 'no link is shared');
  assert.equal(data.text, undefined);
  assert.equal(data.files!.length, 1);
  assert.equal(data.files![0].name, 'demo.mp4');
  assert.equal(data.files![0].type, 'video/mp4');
  assert.equal(data.files![0].size, 3);
});

test('no file sharing (a desktop browser, or no Web Share at all): Share downloads; nothing is fetched or copied', async () => {
  for (const n of [nav({ files: false }).n, { share: async () => {} } as ShareNav, undefined]) {
    const fetched: string[] = []; const downloads: number[] = [];
    assert.equal(canShareFiles(n), false);
    assert.equal(await shareMedia(URL_, 'a.png', 'image/png', deps(n, fetched, downloads)), 'downloaded');
    assert.deepEqual(fetched, []);
    assert.equal(downloads.length, 1);
  }
  assert.match(SHARE_FALLBACK_TITLE, /downloads/);
  assert.match(SHARE_FALLBACK_TITLE, /sign-in/);
});

test('closing the share sheet is not a failure; any other refusal, or a failed fetch, falls back to the download', async () => {
  const fetched: string[] = []; const downloads: number[] = [];
  assert.equal(await shareMedia(URL_, 'a.png', 'image/png', deps(nav({ files: true, reject: 'AbortError' }).n, fetched, downloads)), 'cancelled');
  assert.equal(downloads.length, 0);
  assert.equal(await shareMedia(URL_, 'a.png', 'image/png', deps(nav({ files: true, reject: 'NotAllowedError' }).n, fetched, downloads)), 'downloaded');
  assert.equal(downloads.length, 1);
  const failing = { ...deps(nav({ files: true }).n, fetched, downloads), fetchBlob: async () => { throw new Error('404'); } };
  assert.equal(await shareMedia(URL_, 'a.png', 'image/png', failing), 'downloaded');
  assert.equal(downloads.length, 2);
});

test('bytes fetched ahead (the image the overlay holds) are shared without a second fetch', async () => {
  const { n, shared } = nav({ files: true });
  const fetched: string[] = []; const downloads: number[] = [];
  const ready = new Blob([new Uint8Array([9])], { type: 'image/png' });
  assert.equal(await shareMedia(URL_, 'a.png', 'image/png', { ...deps(n, fetched, downloads), ready }), 'shared');
  assert.deepEqual(fetched, []);
  assert.equal(shared[0].files![0].type, 'image/png');
});

test('the File is labelled from the name; the server\'s sniff decided what it is', () => {
  assert.equal(mediaTypeFor('a.MOV', 'video'), 'video/quicktime');
  assert.equal(mediaTypeFor('rec.webm', 'video'), 'video/webm');
  assert.equal(mediaTypeFor('x.jpg'), 'image/jpeg');
  assert.equal(mediaTypeFor('noext', 'video'), 'video/mp4');
  assert.equal(mediaTypeFor('noext'), 'image/png');
});
