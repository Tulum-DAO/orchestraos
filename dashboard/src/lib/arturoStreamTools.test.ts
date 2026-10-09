/**
 * RED-first: the stream client delivers tool events, and a streamed turn returns what it DID
 * (tools_called, spawned, operator) exactly as the whole-turn path does — onboarding decides on
 * them (DEC-1790747010535469).
 *
 * Run: npx tsx --test src/lib/arturoStreamTools.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { arturoTextStream, arturoTurn } from './arturoStream.js';

function sse(events: Array<[string, unknown]>): Response {
  const body = events.map(([n, d]) => `event: ${n}\ndata: ${JSON.stringify(d)}\n\n`).join('');
  return new Response(new ReadableStream({
    start(c) { c.enqueue(new TextEncoder().encode(body)); c.close(); },
  }), { headers: { 'content-type': 'text/event-stream' } });
}

const TURN: Array<[string, unknown]> = [
  ['turn.start', { turn_id: 't' }],
  ['text.delta', { text: 'Let me check.' }],
  ['tool.call', { call_id: 'c1', name: 'spawn_agent', args_summary: '{"id":"mgr"}' }],
  ['tool.result', { call_id: 'c1', name: 'spawn_agent', ok: true, summary: 'spawned mgr' }],
  ['text.delta', { text: ' Your manager is up.' }],
  ['turn.end', { reply_text: 'Let me check. Your manager is up.', tools_called: ['spawn_agent'],
                 spawned: ['mgr'], operator: { name: 'Mo' } }],
];

test('tool events reach their callbacks, in order with the text', async () => {
  const order: string[] = [];
  (globalThis as any).fetch = async () => sse(TURN);
  await arturoTextStream({}, {
    onDelta: () => order.push('text'),
    onToolCall: (c) => order.push(`call:${c.call_id}`),
    onToolResult: (r) => order.push(`result:${r.call_id}:${r.ok}`),
  });
  assert.deepEqual(order, ['text', 'call:c1', 'result:c1:true', 'text']);
});

test('a streamed turn returns tools_called, spawned and operator from turn.end', async () => {
  (globalThis as any).fetch = async () => sse(TURN);
  const r: any = await arturoTurn('make me a manager', 'c1');
  assert.equal(r.ok, true);
  assert.deepEqual(r.tools_called, ['spawn_agent']);
  assert.deepEqual(r.spawned, ['mgr']);
  assert.deepEqual(r.operator, { name: 'Mo' });
});

test('arturoTurn forwards tool events to its caller', async () => {
  (globalThis as any).fetch = async () => sse(TURN);
  const seen: string[] = [];
  await arturoTurn('x', 'c1', null, {
    onToolCall: (c: any) => seen.push(c.name),
    onToolResult: (r: any) => seen.push(`${r.name}:${r.ok}`),
  } as any);
  assert.deepEqual(seen, ['spawn_agent', 'spawn_agent:true']);
});

// ---- reset: a fallback must not leave the dead attempt on screen -------------------------

test('turn.reset reaches onReset', async () => {
  (globalThis as any).fetch = async () => sse([['turn.start', {}], ['text.delta', { text: 'half' }],
    ['turn.reset', { reason: 'fallback' }], ['text.delta', { text: 'Whole reply.' }],
    ['turn.end', { reply_text: 'Whole reply.', tools_called: [], spawned: [] }]]);
  let resets = 0;
  await arturoTextStream({}, { onReset: () => { resets += 1; } });
  assert.equal(resets, 1);
});

test('arturoTurn resets its caller BEFORE reading back a turn whose stream died', async () => {
  // A stream that dies after turn.start, before turn.end: the server is still running that turn, so
  // arturoTurn reads its stored reply back instead of re-asking (#317 review SF1). Whatever the dead
  // attempt showed (text, tool rows) must be cleared first, or it sits beside the answer.
  // The read-back is by this send's turn id (DEC-1791518421640932), never by matching the thread's text.
  (globalThis as any).fetch = async (url: string) => String(url).includes('/api/arturo/threads/')
    ? new Response(JSON.stringify({ ok: true, turn: { state: 'done', result: { ok: true, status: 200, reply_text: 'Whole reply.' } } }),
        { status: 200, headers: { 'Content-Type': 'application/json' } })
    : sse([['turn.start', {}], ['text.delta', { text: 'half a' }],
        ['tool.call', { call_id: 'c1', name: 'list_agents', args_summary: '{}' }]]);
  const order: string[] = [];
  const r = await arturoTurn('x', 'c1', null, {
    onDelta: () => order.push('delta'),
    onReset: () => order.push('reset'),
  } as any);
  assert.equal(order[order.length - 1], 'reset', 'reset comes after the dead attempt, before the read-back');
  assert.equal(r.ok, true);
  assert.equal(r.reply_text, 'Whole reply.');
});
