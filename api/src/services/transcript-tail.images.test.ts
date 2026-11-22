/**
 * Images in the chat on the LIVE lane: the SSE tailer (services/transcript-tail.ts) reads the log
 * line by line through normalizeTranscript, and its snapshot and delta items carry `images` for a
 * reply that names an image under the agent's roots, exactly as the poll route's do. The chat
 * keeps the stream's items over the poll's once the stream is up, so this lane is the one that
 * must stamp.
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { appendFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { MEDIA_ID } from '../lib/mediaStore.js';

// The data dir (the id table, the agent's roots) is read when the route module loads: set it first.
const DATA = testTmpDir('stream-images-data-');
const PROJECT = join(DATA, 'work', 'stream-agent');
mkdirSync(join(DATA, 'state', 'agents'), { recursive: true });
mkdirSync(PROJECT, { recursive: true });
writeFileSync(join(DATA, 'state', 'agents', 'stream-agent.json'), JSON.stringify({ cwd: PROJECT }));
process.env.ORCHESTRA_DIR = DATA;
const { TranscriptTailer } = await import('./transcript-tail.js');

const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(32, 3)]);
const line = (role: 'user' | 'assistant', content: unknown, uuid: string) =>
  JSON.stringify({ type: role, uuid, timestamp: '2026-11-22T06:00:00Z', message: { role, content } });

test('the tailer\'s snapshot and delta items carry images, like the poll route\'s', () => {
  const dir = testTmpDir('stream-images-');
  const path = join(dir, 'session-s.jsonl');
  const shot = join(PROJECT, 'before.png');
  const made = join(PROJECT, 'chart.png');
  writeFileSync(shot, PNG);
  writeFileSync(made, PNG);
  // Newline-terminated, as every real agent log is: a tailer rightly holds an unterminated last line back.
  writeFileSync(path, [line('user', 'show me', 'u1'), line('assistant', `Here: ${shot} and /etc/not-mine.png`, 'u2')].join('\n') + '\n');
  const tailer = new TranscriptTailer({ agentId: 'stream-agent', resolve: () => ({ path, sid: 'session-s' }), limit: 150 });
  const events: any[] = [];
  tailer.subscribe((ev) => events.push(ev));

  assert.equal(events[0].type, 'snapshot');
  const reply = events[0].items.find((it: any) => it.kind === 'text' && it.role === 'assistant');
  assert.ok(reply, 'the reply is in the snapshot');
  assert.deepEqual(reply.images.map((i: any) => i.path), [shot]);
  assert.match(reply.images[0].id, MEDIA_ID);
  const node = events[0].render_items.find((n: any) => n.kind === 'assistant');
  assert.deepEqual(node.images, reply.images);

  // A Write lands while the stream is up: the delta carries the image too.
  appendFileSync(path, line('assistant', [{ type: 'tool_use', id: 't1', name: 'Write', input: { file_path: made, content: '…' } }], 'u3') + '\n');
  tailer.tick();
  const delta = events[events.length - 1];
  assert.equal(delta.type, 'delta');
  const w = delta.items.find((it: any) => it.kind === 'tool_use');
  assert.equal(w.images.length, 1);
  assert.equal(w.images[0].path, made);
  assert.match(w.images[0].id, MEDIA_ID);
});
