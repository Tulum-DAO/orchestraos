import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeTranscript } from './chat-transcript.js';

const T = (s: number) => `2026-01-01T10:00:${String(s).padStart(2, '0')}.000Z`;
const user = (uuid: string, s: number, text: string) =>
  JSON.stringify({ type: 'user', uuid, timestamp: T(s), message: { role: 'user', content: text } });
const say = (uuid: string, s: number, text: string) =>
  JSON.stringify({ type: 'assistant', uuid, timestamp: T(s), message: { role: 'assistant', model: 'claude-opus-5-5', content: [{ type: 'text', text }] } });

test('a window that cuts off the opener keeps the turn tags, and render nodes carry them', () => {
  const lines = [user('op', 1, 'please summarize the logs'), say('m1', 2, 'reading them'), say('m2', 3, 'still reading'),
                 say('m3', 4, 'summary: all quiet')];
  const env = normalizeTranscript(lines, 'gm', 's', 2);               // the window drops the opener
  assert.deepEqual(env.items.map((i: any) => [i.uuid, i.audience, i.exchange]),
    [['m2', 'agent', 'op'], ['m3', 'user', 'op']]);
  assert.deepEqual(env.render_items.map((r: any) => [r.kind, r.audience, r.exchange]),
    [['assistant', 'agent', 'op'], ['assistant', 'user', 'op']]);
});

test('a stop-hook line the route drops (isMeta) never splits his turn: his first and last replies are drawn', () => {
  const line = (o: any) => JSON.stringify({ timestamp: '2026-10-22T05:00:00Z', ...o });
  const lines = [
    line({ type: 'user', uuid: 'm1', message: { role: 'user', content: 'ship it' } }),
    line({ type: 'assistant', uuid: 'a1', message: { role: 'assistant', content: [{ type: 'text', text: 'shipping now' }] } }),
    line({ type: 'user', uuid: 'h1', isMeta: true, message: { role: 'user', content: 'Stop hook feedback:\n[QUEUE-DIGEST] 1 pending' } }),
    line({ type: 'assistant', uuid: 'a2', message: { role: 'assistant', content: [{ type: 'text', text: 'handled the digest' }] } }),
    line({ type: 'assistant', uuid: 'a3', message: { role: 'assistant', content: [{ type: 'text', text: 'shipped' }] } }),
  ];
  const { items } = normalizeTranscript(lines, 'gm', 's', 150) as any;
  assert.ok(!items.some((i: any) => i.uuid === 'h1'), 'the hook line is not an item');
  assert.deepEqual(items.map((i: any) => [i.text, i.audience]),
    [['ship it', 'user'], ['shipping now', 'user'], ['handled the digest', 'agent'], ['shipped', 'user']]);
  assert.ok(items.every((i: any) => i.exchange === 'm1'));
});
