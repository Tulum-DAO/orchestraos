/**
 * Images in the chat: an agent's reply that names an image file, or a Write that made one, carries
 * `images` (an opaque id per path) on its item and its render node when the file is there under
 * the agent's own roots; a path outside them, or not there, gets no id; the words are untouched.
 * The poll route and the SSE tailer share finishWindow, so normalizeTranscript is the one place
 * to prove the stamp (the tailer's own run is tests/transcript-stream.images.test.ts).
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
mkdirSync(join(DATA, 'state', 'shots', 'gm'), { recursive: true });
mkdirSync(join(DATA, 'state', 'uploads'), { recursive: true });
mkdirSync(join(PROJECT, 'shots'), { recursive: true });
writeFileSync(join(DATA, 'state', 'agents', 'gm.json'), JSON.stringify({ cwd: PROJECT }));
process.env.ORCHESTRA_DIR = DATA;
const { normalizeTranscript, stampImages, buildRenderItems } = await import('./chat-transcript.js');

const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(32, 7)]);
const claude = (role: 'user' | 'assistant', content: unknown, ts = '2026-11-22T05:00:00Z') =>
  JSON.stringify({ type: role, uuid: `u-${Math.random()}`, timestamp: ts, message: { role, content } });

test('a reply naming an image under the agent\'s roots carries its opaque id; the words stay', () => {
  const shot = join(PROJECT, 'shots', 'after.png');
  const own = join(DATA, 'state', 'shots', 'gm', 'x.png');
  writeFileSync(shot, PNG);
  writeFileSync(own, PNG);
  const lines = [
    claude('user', 'show me'),
    claude('assistant', `Here it is: ${shot} (and my shot folder's ${own})`),
  ];
  const { items, render_items } = normalizeTranscript(lines, 'gm', 's', 150);
  const reply = items.find((it: any) => it.kind === 'text' && it.role === 'assistant');
  assert.ok(reply.text.includes(shot), 'the path stays readable as text');
  assert.deepEqual(reply.images.map((i: any) => i.path), [shot, own]);
  assert.deepEqual(reply.images.map((i: any) => i.name), ['after.png', 'x.png']);
  for (const i of reply.images) { assert.match(i.id, MEDIA_ID); assert.ok(!i.id.includes('/')); }
  const node = render_items.find((n: any) => n.kind === 'assistant');
  assert.deepEqual(node.images, reply.images);
  const again = normalizeTranscript(lines, 'gm', 's', 150).items.find((it: any) => it.kind === 'text' && it.role === 'assistant');
  assert.equal(again.images[0].id, reply.images[0].id, 'the same path, the same id');
});

test('no id: a path outside the roots, another client\'s upload under the data dir, a file that is not there, a user line, a Read', () => {
  writeFileSync(join(DATA, 'state', 'uploads', 'client-b.png'), PNG);
  const lines = [
    claude('user', `look at ${join(PROJECT, 'a.png')}`),
    claude('assistant', [
      { type: 'text', text: `See /etc/wallpaper.png, ${join(DATA, 'state', 'uploads', 'client-b.png')} and ${join(PROJECT, 'not-yet.png')}` },
      { type: 'tool_use', id: 't1', name: 'Read', input: { file_path: join(PROJECT, 'shots', 'after.png') } },
    ]),
    claude('user', [{ type: 'tool_result', tool_use_id: 't1', content: 'bytes' }]),
  ];
  const { items, render_items } = normalizeTranscript(lines, 'gm', 's', 150);
  for (const it of items) assert.equal('images' in it, false, JSON.stringify(it.kind));
  for (const n of render_items) assert.equal('images' in n, false, n.kind);
});

test('a Write that made an image carries it on the tool item and its render node', () => {
  const made = join(PROJECT, 'chart.png');
  writeFileSync(made, PNG);
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

test('stampImages with an injected stamper: it decides, items are never dropped or reordered', () => {
  const asked: string[][] = [];
  const refsFor = (_a: string, paths: string[]) => { asked.push(paths); return paths.filter((p) => p.startsWith('/srv/p/')).map((p) => ({ id: 'f'.repeat(32), path: p, name: p.split('/').pop()! })); };
  const items = [
    { kind: 'text', role: 'assistant', text: 'in /srv/p/a.png, out /srv/q/b.png' },
    { kind: 'thinking', role: 'assistant', text: '/srv/p/think.png' },
    { kind: 'text', role: 'user', text: '/srv/p/user.png' },
    { kind: 'rotation' },
  ];
  const out = stampImages(items, 'gm', refsFor);
  assert.equal(out.length, items.length);
  assert.deepEqual(out[0].images, [{ id: 'f'.repeat(32), path: '/srv/p/a.png', name: 'a.png' }]);
  assert.deepEqual(asked, [['/srv/p/a.png', '/srv/q/b.png']]);
  assert.equal('images' in out[1], false);
  assert.strictEqual(out[2], items[2], 'an untouched item is the same object');
  assert.strictEqual(out[3], items[3]);
  const nodes = buildRenderItems(out);
  assert.deepEqual(nodes[0].images, out[0].images);
  assert.equal('images' in nodes[1], false);
});
