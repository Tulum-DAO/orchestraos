import { test } from 'node:test';
import assert from 'node:assert/strict';
import { withCallCards, fetchCallCards, type AgentCallRow } from './callCards.ts';

const line = (ts: string, text = 'x') => ({ kind: 'text', role: 'assistant', text, ts });
const call = (id: string, started: string, over: Partial<AgentCallRow> = {}): AgentCallRow =>
  ({ call_id: id, status: 'ended', started_at: started, duration_s: 38, rating: null, marker: `[voice-call: ${id} /x/${id}.json]`, handed: false, ...over });

test('an ended call is placed where it started, as the [voice-call:] user row the chat draws as the card', () => {
  const items = [line('2026-11-02T00:30:00Z', 'a'), line('2026-11-02T00:40:00Z', 'b')];
  const out = withCallCards(items, [call('vc_aaaa1111', '2026-11-02T00:33:48Z')]);
  assert.equal(out.length, 3);
  assert.deepEqual(out[1], { kind: 'text', role: 'user', text: '[voice-call: vc_aaaa1111 /x/vc_aaaa1111.json]',
    ts: '2026-11-02T00:33:48Z', call_id: 'vc_aaaa1111', duration_s: 38, rating: null, audience: 'user' });
});

test('never twice, never a live or handed-off call, never one older than the window', () => {
  const items = [line('2026-11-02T00:30:00Z'), { ...line('2026-11-02T00:31:00Z'), call_id: 'vc_held1111' }];
  const out = withCallCards(items, [
    call('vc_held1111', '2026-11-02T00:31:00Z'),
    call('vc_live1111', '2026-11-02T00:32:00Z', { status: 'live', duration_s: null }),
    call('vc_hand1111', '2026-11-02T00:33:00Z', { handed: true }),
    call('vc_old11111', '2026-11-01T00:00:00Z'),
    call('vc_nomk1111', '2026-11-02T00:34:00Z', { marker: undefined }),
  ]);
  assert.equal(out, items);
});

test('the newest call goes last; an empty window takes every call', () => {
  const out = withCallCards([line('2026-11-02T00:30:00Z')], [call('vc_new11111', '2026-11-02T00:50:00Z')]);
  assert.equal((out[1] as { call_id?: string }).call_id, 'vc_new11111');
  assert.equal(withCallCards([], [call('vc_any11111', '2026-11-02T00:50:00Z')]).length, 1);
});

test('the list read: calls from the route, nothing on any failure', async () => {
  const ok = (async () => new Response(JSON.stringify({ ok: true, calls: [call('vc_f1111111', '2026-11-02T00:00:00Z')] }))) as unknown as typeof fetch;
  assert.equal((await fetchCallCards('a', ok)).length, 1);
  assert.deepEqual(await fetchCallCards('a', (async () => new Response('x', { status: 500 })) as unknown as typeof fetch), []);
  assert.deepEqual(await fetchCallCards('a', (async () => { throw new Error('offline'); }) as unknown as typeof fetch), []);
});
