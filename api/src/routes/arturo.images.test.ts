/**
 * Images in the chat on Arturo's thread: GET /api/arturo/threads/:id relays the gateway's turns
 * and stamps `image_refs` on each of HIS replies that names an image he may show (the stamper
 * decides, under his own roots). The client never sends a path; a user turn and a path the
 * stamper refuses get nothing; the words are untouched.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { createArturoRouter, type ArturoDeps } from './arturo.js';

function deps(turns: unknown[], asked: string[][]): ArturoDeps {
  return {
    enabled: () => true,
    token: () => 'test-token',
    fetchJson: async () => ({ status: 200, body: { ok: true, id: 'c1', title: 't', turns } }),
    forwardStream: async () => ({ status: 200, body: { ok: true, text: '' } }),
    installRole: () => 'admin',
    imageRefs: (agent: string, paths: string[]) => {
      asked.push([agent, ...paths]);
      return paths.filter((p: string) => p.startsWith('/srv/arturo/')).map((p: string) => ({ id: 'e'.repeat(32), path: p, name: p.split('/').pop()! }));
    },
  } as unknown as ArturoDeps;
}

async function get(d: ArturoDeps, path: string) {
  const app = express();
  app.use(express.json());
  app.use('/api/arturo', createArturoRouter(d));
  const server = app.listen(0);
  const { port } = server.address() as AddressInfo;
  try {
    const r = await fetch(`http://127.0.0.1:${port}${path}`);
    return { status: r.status, json: await r.json() };
  } finally { server.close(); }
}

test('a thread read stamps image_refs on his replies that name an image he may show', async () => {
  const asked: string[][] = [];
  const turns = [
    { role: 'user', content: 'show me /srv/arturo/out/a.png', ts: 1 },
    { role: 'assistant', content: 'Here: /srv/arturo/out/a.png and /etc/wall.png', ts: 2 },
    { role: 'assistant', content: 'nothing here', ts: 3 },
    { role: 'assistant', content: 'only /etc/wall.png', ts: 4 },
  ];
  const r = await get(deps(turns, asked), '/api/arturo/threads/c1');
  assert.equal(r.status, 200);
  const [u, a1, a2, a3] = r.json.turns;
  assert.equal('image_refs' in u, false, 'the operator\'s turn is never stamped');
  assert.deepEqual(a1.image_refs, [{ id: 'e'.repeat(32), path: '/srv/arturo/out/a.png', name: 'a.png' }]);
  assert.equal(a1.content, 'Here: /srv/arturo/out/a.png and /etc/wall.png', 'the words are untouched');
  assert.equal('image_refs' in a2, false);
  assert.equal('image_refs' in a3, false);
  // The stamper is asked for Arturo's own agent, with the paths his reply names and nothing more.
  assert.deepEqual(asked, [['arturo', '/srv/arturo/out/a.png', '/etc/wall.png'], ['arturo', '/etc/wall.png']]);
});

test('a thread read with no turns, or a non-200 from the gateway, passes through untouched', async () => {
  const asked: string[][] = [];
  const d = deps([], asked);
  const r = await get(d, '/api/arturo/threads/c1');
  assert.deepEqual(r.json.turns, []);
  const down = { ...d, fetchJson: async () => ({ status: 502, body: { ok: false, error: 'x' } }) } as ArturoDeps;
  const r2 = await get(down, '/api/arturo/threads/c1');
  assert.equal(r2.status, 502);
  assert.deepEqual(r2.json, { ok: false, error: 'x' });
  assert.deepEqual(asked, []);
});
