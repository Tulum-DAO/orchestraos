// Codex reports a response's tokens AFTER its items (token_count): the usage lands on an item the
// SSE tailer already sent. Codex r1 on #466: deltas only carry appended items, so a streaming client
// would never get it. Run: npx tsx --test src/services/transcript-tail.usage.test.ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, appendFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { TranscriptTailer, type TailEvent } from './transcript-tail.js';

const rec = (ordinal: number, type: string, payload: any) =>
  JSON.stringify({ ordinal, type, timestamp: '2026-10-22T05:00:00Z', payload }) + '\n';

test('a Codex token_count that lands on an already-sent item reaches streaming clients', () => {
  const f = join(mkdtempSync(join(tmpdir(), 'tail-usage-')), 'rollout.jsonl');
  writeFileSync(f, rec(1, 'session_meta', { id: 's' })
    + rec(2, 'response_item', { type: 'message', role: 'user', content: [{ type: 'input_text', text: 'go' }] })
    + rec(3, 'response_item', { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'done' }] }));
  const t = new TranscriptTailer({ agentId: 'cx', resolve: () => ({ path: f, sid: 's' }) });
  const got: TailEvent[] = [];
  t.subscribe((ev) => got.push(ev));
  assert.equal(got.length, 1);
  assert.equal(got[0].items.find((i: any) => i.text === 'done')?.usage, undefined);
  appendFileSync(f, rec(4, 'event_msg', { type: 'token_count', info: {
    total_token_usage: { total_tokens: 105 }, last_token_usage: { input_tokens: 100, output_tokens: 5 } } }));
  t.tick();
  const last = got[got.length - 1];
  assert.ok(got.length === 2, 'the usage change is broadcast');
  assert.equal(last.type, 'snapshot');
  assert.equal(last.items.find((i: any) => i.text === 'done')?.usage?.output_tokens, 5);
  t.tick();
  assert.equal(got.length, 2, 'and only once');
});

test('plain growth with no usage change is still a delta, not a snapshot', () => {
  const f = join(mkdtempSync(join(tmpdir(), 'tail-usage-')), 'rollout.jsonl');
  const say = (o: number, text: string) => rec(o, 'response_item', { type: 'message', role: 'assistant', content: [{ type: 'output_text', text }] });
  writeFileSync(f, rec(1, 'session_meta', { id: 's' }) + say(2, 'one')
    + rec(3, 'event_msg', { type: 'token_count', info: { total_token_usage: { total_tokens: 105 }, last_token_usage: { input_tokens: 100, output_tokens: 5 } } }));
  const t = new TranscriptTailer({ agentId: 'cx', resolve: () => ({ path: f, sid: 's' }) });
  const got: TailEvent[] = [];
  t.subscribe((ev) => got.push(ev));
  appendFileSync(f, say(4, 'two'));
  t.tick();
  assert.equal(got.length, 2);
  assert.equal(got[1].type, 'delta');
  assert.deepEqual(got[1].items.map((i: any) => i.text), ['two']);
});

test('a client that missed the restated snapshot gets it on resume (the event id carries a revision)', () => {
  const f = join(mkdtempSync(join(tmpdir(), 'tail-usage-')), 'rollout.jsonl');
  writeFileSync(f, rec(1, 'session_meta', { id: 's' })
    + rec(2, 'response_item', { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'done' }] }));
  const t = new TranscriptTailer({ agentId: 'cx', resolve: () => ({ path: f, sid: 's' }) });
  const first: TailEvent[] = [];
  const unsub = t.subscribe((ev) => first.push(ev));
  const lastSeen = first[first.length - 1].id;                 // then this client drops
  unsub();
  appendFileSync(f, rec(3, 'event_msg', { type: 'token_count', info: {
    total_token_usage: { total_tokens: 105 }, last_token_usage: { input_tokens: 100, output_tokens: 5 } } }));
  const keep: TailEvent[] = [];
  t.subscribe((ev) => keep.push(ev));                          // another client keeps the tailer ticking
  t.tick();
  const back: TailEvent[] = [];
  t.subscribe((ev) => back.push(ev), lastSeen);
  assert.equal(back.length, 1);
  assert.equal(back[0].type, 'snapshot');
  assert.equal(back[0].items.find((i: any) => i.text === 'done')?.usage?.output_tokens, 5);
});
