/**
 * GET /api/media/:id (routes/media.ts), end to end over a listening app: the bytes and headers of a
 * served image, and a 404 for everything the allow-list, the sniff, the scope rule, or the id table
 * refuses. The URL never carries a path or a credential, and there is no route that takes one.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { mkdirSync, renameSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { testTmpDir } from '../lib/testTmp.js';
import { MediaStore, MEDIA_ID } from '../lib/mediaStore.js';
import { createMediaRouter } from './media.js';

const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.from('fixture-bytes')]);

function fixture() {
  const base = testTmpDir('media-route-');
  const project = join(base, 'project');
  const outside = join(base, 'outside');
  mkdirSync(project, { recursive: true });
  mkdirSync(outside, { recursive: true });
  writeFileSync(join(project, 'shot.png'), PNG);
  writeFileSync(join(project, 'page.png'), '<html><script>alert(1)</script></html>');
  writeFileSync(join(outside, 'secret.png'), PNG);
  symlinkSync(join(outside, 'secret.png'), join(project, 'link.png'));
  const store = new MediaStore(join(base, 'ids.json'));
  const tags: Record<string, string[]> = { gm: ['client:acme'], other: [] };
  const router = createMediaRouter({ store, roots: (a) => (a === 'gm' ? [project] : []), resolveTags: (a) => tags[a] });
  return { base, project, outside, store, router };
}

async function withApp(router: express.Router, fn: (base: string) => Promise<void>) {
  const app = express();
  app.use(express.json());
  app.use('/api/media', router);
  const server = app.listen(0);
  const { port } = server.address() as AddressInfo;
  try { await fn(`http://127.0.0.1:${port}`); } finally { server.close(); }
}

function trusted() {
  const prev = process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
  process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = '1';
  return () => {
    if (prev === undefined) delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
    else process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = prev;
  };
}

test('a referenced image is served by id: its bytes, its sniffed type, private caching, nosniff; HEAD answers the length', async () => {
  const f = fixture();
  const id = f.store.idFor('gm', join(f.project, 'shot.png'));
  assert.match(id, MEDIA_ID);
  await withApp(f.router, async (base) => {
    const r = await fetch(`${base}/api/media/${id}`);
    assert.equal(r.status, 200);
    assert.equal(r.headers.get('content-type'), 'image/png');
    assert.equal(r.headers.get('content-length'), String(PNG.length));
    assert.match(r.headers.get('cache-control') || '', /^private\b/);
    assert.equal(r.headers.get('x-content-type-options'), 'nosniff');
    assert.match(r.headers.get('content-disposition') || '', /^inline; filename="shot\.png"$/);
    assert.deepEqual(Buffer.from(await r.arrayBuffer()), PNG);
    const d = await fetch(`${base}/api/media/${id}?download`);
    assert.match(d.headers.get('content-disposition') || '', /^attachment; filename="shot\.png"$/);
    assert.deepEqual(Buffer.from(await d.arrayBuffer()), PNG);
    const h = await fetch(`${base}/api/media/${id}`, { method: 'HEAD' });
    assert.equal(h.status, 200);
    assert.equal(h.headers.get('content-length'), String(PNG.length));
    // Served twice more: the descriptor is closed each time (no leak that would show as EMFILE here).
    for (let i = 0; i < 3; i++) assert.equal((await fetch(`${base}/api/media/${id}`)).status, 200);
  });
});

test('404 for an unknown id, a malformed id, and a path-shaped id', async () => {
  const f = fixture();
  await withApp(f.router, async (base) => {
    for (const bad of ['0'.repeat(32), 'nope', '..%2F..%2Fetc%2Fpasswd', encodeURIComponent(join(f.project, 'shot.png'))]) {
      const r = await fetch(`${base}/api/media/${bad}`);
      assert.equal(r.status, 404, bad);
      assert.deepEqual(await r.json(), { error: 'not found' });
    }
  });
});

test('SABOTAGE: an id minted for a path outside the roots, a symlink, traversal, or a non-image is 404', async () => {
  const f = fixture();
  const ids = {
    outside: f.store.idFor('gm', join(f.outside, 'secret.png')),
    symlink: f.store.idFor('gm', join(f.project, 'link.png')),
    traversal: f.store.idFor('gm', `${f.project}/../outside/secret.png`),
    html: f.store.idFor('gm', join(f.project, 'page.png')),
    missing: f.store.idFor('gm', join(f.project, 'gone.png')),
    noRoots: f.store.idFor('other', join(f.project, 'shot.png')),   // a seat with no roots serves nothing
  };
  await withApp(f.router, async (base) => {
    for (const [why, id] of Object.entries(ids)) {
      const r = await fetch(`${base}/api/media/${id}`);
      assert.equal(r.status, 404, why);
      assert.deepEqual(await r.json(), { error: 'not found' }, why);
    }
  });
});

test('SABOTAGE TOCTOU: the file behind a good id swapped for a link after it was served is 404 on the next request', async () => {
  const f = fixture();
  const target = join(f.project, 'shot.png');
  const id = f.store.idFor('gm', target);
  await withApp(f.router, async (base) => {
    assert.equal((await fetch(`${base}/api/media/${id}`)).status, 200);
    renameSync(target, join(f.project, 'moved.png'));
    symlinkSync(join(f.outside, 'secret.png'), target);
    const r = await fetch(`${base}/api/media/${id}`);
    assert.equal(r.status, 404);
  });
});

test('the chat\'s scope rule: a principal who cannot see the agent gets the unknown-id 404', async () => {
  const f = fixture();
  const id = f.store.idFor('gm', join(f.project, 'shot.png'));
  const restore = trusted();
  try {
    await withApp(f.router, async (base) => {
      const scoped = { 'x-orchestra-user': 'client1', 'x-orchestra-role': 'viewer', 'x-orchestra-client': 'someone-else' };
      const r = await fetch(`${base}/api/media/${id}`, { headers: scoped });
      assert.equal(r.status, 404);
      assert.deepEqual(await r.json(), { error: 'not found' });
      const anon = await fetch(`${base}/api/media/${id}`);
      assert.equal(anon.status, 404);
      const ok = await fetch(`${base}/api/media/${id}`, { headers: { ...scoped, 'x-orchestra-client': 'acme' } });
      assert.equal(ok.status, 200);
    });
  } finally { restore(); }
});

test('there is no route that takes a path: /resolve is gone, and an unreferenced file has no id to be asked for', async () => {
  const f = fixture();
  await withApp(f.router, async (base) => {
    const r = await fetch(`${base}/api/media/resolve`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agent: 'gm', path: join(f.project, 'shot.png') }) });
    assert.equal(r.status, 404);
    assert.equal(f.store.has('gm', join(f.project, 'shot.png')), null, 'nothing was minted');
  });
});
