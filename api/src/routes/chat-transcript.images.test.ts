/**
 * Images in the chat: an agent's reply that names an image file, or a Write that made one, carries
 * `images` (an opaque id per path) on its item and its render node; a path outside the agent's
 * roots gets no id; the words are untouched. The poll route and the SSE tailer share finishWindow,
 * so normalizeTranscript is the one place to prove it.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { testTmpDir } from '../lib/testTmp.js';
import { MEDIA_ID } from '../lib/mediaStore.js';

// The data dir (and so the id table and the agent's roots) is read at import, so it is set first.
const DATA = testTmpDir('images-data-');
const PROJECT = join(DATA, 'work', 'gm');
mkdirSync(join(DATA, 'state', 'agents'), { recursive: true });
mkdirSync(PROJECT, { recursive: true });
writeFileSync(join(DATA, 'state', 'agents', 'gm.json'), JSON.stringify({ cwd: PROJECT }));
process.env.ORCHESTRA_DIR = DATA;
const { normalizeTranscript, stampImages, buildRenderItems } = await import('./chat-transcript.js');

const claude = (role: 'user' | 'assistant', content: unknown, ts = '2026-11-22T05:00:00Z') =>
  JSON.stringify({ type: role, uuid: `u-${Math.random()}`, timestamp: ts, message: { role, content } });

test('a reply naming an image under the agent\'s roots carries its opaque id; the words stay', () => {
  const shot = join(PROJECT, 'shots', 'after.png');
  const lines = [
    claude('user', 'show me'),
    claude('assistant', `Here it is: ${shot} (and the data dir's ${join(DATA, 'state', 'uploads', 'x.jpg')})`),
  ];
  const { items, render_items } = normalizeTranscript(lines, 'gm', 's', 150);
  const reply = items.find((it: any) => it.kind === 'text' && it.role === 'assistant');
  assert.ok(reply.text.includes(shot), 'the path stays readable as text');
  assert.equal(reply.images.length, 2);
  assert.deepEqual(reply.images.map((i: any) => i.path), [shot, join(DATA, 'state', 'uploads', 'x.jpg')]);
  assert.deepEqual(reply.images.map((i: any) => i.name), ['after.png', 'x.jpg']);
  for (const i of reply.images) { assert.match(i.id, MEDIA_ID); assert.ok(!i.id.includes('/')); }
  // The same on the server-paired render node.
  const node = render_items.find((n: any) => n.kind === 'assistant');
  assert.deepEqual(node.images, reply.images);
  // Stable across reads: the same path, the same id.
  const again = normalizeTranscript(lines, 'gm', 's', 150).items.find((it: any) => it.kind === 'text' && it.role === 'assistant');
  assert.equal(again.images[0].id, reply.images[0].id);
});

test('a path outside the roots gets no id; a user line and a Read never do', () => {
  const lines = [
    claude('user', `look at ${join(PROJECT, 'a.png')}`),
    claude('assistant', [
      { type: 'text', text: 'See /etc/wallpaper.png and /root/secret.png' },
      { type: 'tool_use', id: 't1', name: 'Read', input: { file_path: join(PROJECT, 'a.png') } },
    ]),
    claude('user', [{ type: 'tool_result', tool_use_id: 't1', content: 'bytes' }]),
  ];
  const { items, render_items } = normalizeTranscript(lines, 'gm', 's', 150);
  for (const it of items) assert.equal('images' in it, false, JSON.stringify(it.kind));
  for (const n of render_items) assert.equal('images' in n, false, n.kind);
});

test('a Write that made an image carries it on the tool item and its render node', () => {
  const made = join(PROJECT, 'chart.png');
  const lines = [
    claude('user', 'plot it'),
    claude('assistant', [{ type: 'tool_use', id: 't1', name: 'Write', input: { file_path: made, content: '...' } }]),
    claude('user', [{ type: 'tool_result', tool_use_id: 't1', content: 'ok' }]),
    claude('assistant', [{ type: 'tool_use', id: 't2', name: 'Write', input: { file_path: join(PROJECT, 'notes.md'), content: '#' } }]),
    claude('user', [{ type: 'tool_result', tool_use_id: 't2', content: 'ok' }]),
  ];
  const { items, render_items } = normalizeTranscript(lines, 'gm', 's', 150);
  const [w1, w2] = items.filter((it: any) => it.kind === 'tool_use');
  assert.equal(w1.images.length, 1);
  assert.equal(w1.images[0].path, made);
  assert.equal('images' in w2, false);
  const [n1, n2] = render_items.filter((n: any) => n.kind === 'tool');
  assert.deepEqual(n1.images, w1.images);
  assert.equal('images' in n2, false);
});

test('stampImages with injected deps: the roots decide, the table mints, items are never dropped', () => {
  const minted: string[] = [];
  const deps = { roots: ['/srv/p'], idFor: (_a: string, p: string) => { minted.push(p); return 'f'.repeat(32); } };
  const items = [
    { kind: 'text', role: 'assistant', text: 'in /srv/p/a.png, out /srv/q/b.png' },
    { kind: 'thinking', role: 'assistant', text: '/srv/p/think.png' },
    { kind: 'text', role: 'user', text: '/srv/p/user.png' },
    { kind: 'rotation' },
  ];
  const out = stampImages(items, 'gm', deps);
  assert.equal(out.length, items.length);
  assert.deepEqual(out[0].images, [{ id: 'f'.repeat(32), path: '/srv/p/a.png', name: 'a.png' }]);
  assert.deepEqual(minted, ['/srv/p/a.png']);
  assert.equal('images' in out[1], false);
  assert.equal('images' in out[2], false);
  assert.strictEqual(out[3], items[3]);
  assert.strictEqual(out[2], items[2], 'an untouched item is the same object');
  // Render nodes carry what the items carry, and nothing where there is nothing.
  const nodes = buildRenderItems(out);
  assert.deepEqual(nodes[0].images, out[0].images);
  assert.equal('images' in nodes[1], false);
});
