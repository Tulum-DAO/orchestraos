/**
 * The SSE tailer stamps the same message ref as the poll route (reactions build step 1): `<sid>~<uuid>#<part>` on
 * snapshot and delta items, part numbered over the whole read (not the snapshot window); a cut-turn line and a
 * session-less log get none.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/services/transcript-tail.ref.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, appendFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { TranscriptTailer, type TailEvent } from './transcript-tail.js';

const user = (uuid: string, text: string) => JSON.stringify({
  type: 'user', uuid, timestamp: '2026-11-02T10:00:00Z', message: { role: 'user', content: text } }) + '\n';
const twoBlocks = (uuid: string) => JSON.stringify({
  type: 'assistant', uuid, timestamp: '2026-11-02T10:00:01Z',
  message: { id: `m-${uuid}`, role: 'assistant', content: [
    { type: 'text', text: 'looking' }, { type: 'tool_use', id: `t-${uuid}`, name: 'Read', input: { file_path: '/a' } },
  ] },
}) + '\n';
const log = (body: string) => {
  const f = join(mkdtempSync(join(tmpdir(), 'tail-ref-')), 'log.jsonl');
  writeFileSync(f, body);
  return f;
};
const NONE = { read: () => [], sig: () => '' };

test('snapshot and delta items carry <sid>~<uuid>#<part>', () => {
  const f = log(user('u1', 'hello') + twoBlocks('a1'));
  const t = new TranscriptTailer({ agentId: 'zz-tail-ref', resolve: () => ({ path: f, sid: 'S1' }), interrupted: NONE });
  const got: TailEvent[] = [];
  t.subscribe((ev) => got.push(ev));
  assert.deepEqual(got[0].items.map((i: any) => i.ref), ['S1~u1#0', 'S1~a1#0', 'S1~a1#1']);
  appendFileSync(f, twoBlocks('a2'));
  t.tick();
  assert.equal(got[1].type, 'delta');
  assert.deepEqual(got[1].items.map((i: any) => i.ref), ['S1~a2#0', 'S1~a2#1']);
});

test('a snapshot window that starts inside a line still numbers from the line', () => {
  const f = log(user('u1', 'hello') + twoBlocks('a1'));
  const t = new TranscriptTailer({ agentId: 'zz-tail-ref', resolve: () => ({ path: f, sid: 'S1' }), limit: 1, interrupted: NONE });
  const got: TailEvent[] = [];
  t.subscribe((ev) => got.push(ev));
  assert.deepEqual(got[0].items.map((i: any) => i.ref), ['S1~a1#1']);
});

test('a cut-turn line gets no ref; the items around it keep theirs', () => {
  const f = log(user('u1', 'hello') + twoBlocks('a1'));
  const t = new TranscriptTailer({
    agentId: 'zz-tail-ref', resolve: () => ({ path: f, sid: 'S1' }),
    interrupted: { read: () => [{ id: 'x1', text: 'hello' }], sig: () => 'x1' },
  });
  const got: TailEvent[] = [];
  t.subscribe((ev) => got.push(ev));
  const byUuid = got[0].items.map((i: any) => [i.uuid, i.ref ?? null]);
  assert.deepEqual(byUuid.find(([u]: any) => u === 'interrupted:x1'), ['interrupted:x1', null]);
  assert.deepEqual(byUuid.filter(([, r]: any) => r).map(([, r]: any) => r), ['S1~u1#0', 'S1~a1#0', 'S1~a1#1']);
});

test('no session id: no refs (no stable identity to give)', () => {
  const f = log(user('u1', 'hello'));
  const t = new TranscriptTailer({ agentId: 'zz-tail-ref', resolve: () => ({ path: f, sid: null }), interrupted: NONE });
  const got: TailEvent[] = [];
  t.subscribe((ev) => got.push(ev));
  assert.equal(got[0].items[0].ref, undefined);
});
