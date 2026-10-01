/**
 * RED-first: a streamed turn is a sequence of parts — text, a tool card, more text — in the
 * order they happened (DEC-1790747010535469). Pure, so it is tested without a DOM.
 *
 * Run: npx tsx --test src/lib/turnParts.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyTextDelta, applyToolCall, applyToolResult, groupParts, type TurnPart } from './turnParts.js';

const call = (call_id: string, name = 'list_agents', args_summary = '{}') => ({ call_id, name, args_summary });

test('consecutive deltas grow one text part', () => {
  let p: TurnPart[] = [];
  p = applyTextDelta(p, 'Let me ');
  p = applyTextDelta(p, 'check.');
  assert.deepEqual(p, [{ kind: 'text', text: 'Let me check.' }]);
});

test('text, then a tool, then text — three parts in that order', () => {
  let p: TurnPart[] = [];
  p = applyTextDelta(p, 'Let me check.');
  p = applyToolCall(p, call('c1'));
  p = applyToolResult(p, { call_id: 'c1', name: 'list_agents', ok: true, summary: '4 agents' });
  p = applyTextDelta(p, 'Four are running.');
  assert.deepEqual(p.map((x) => x.kind), ['text', 'tool', 'text']);
  assert.equal((p[2] as any).text, 'Four are running.');
});

test('a call is running until its result arrives, then ok or failed', () => {
  let p = applyToolCall([], call('c1'));
  assert.equal((p[0] as any).status, 'running');
  p = applyToolResult(p, { call_id: 'c1', name: 'list_agents', ok: false, summary: 'error: gone' });
  assert.equal((p[0] as any).status, 'failed');
  assert.equal((p[0] as any).summary, 'error: gone');
});

test('results pair by call_id, not by position — two calls to one tool', () => {
  let p: TurnPart[] = [];
  p = applyToolCall(p, call('c1', 'list_agents', '{"tier":1}'));
  p = applyToolCall(p, call('c2', 'list_agents', '{"tier":2}'));
  p = applyToolResult(p, { call_id: 'c2', name: 'list_agents', ok: true, summary: 'tier 2' });
  assert.equal((p[0] as any).status, 'running');
  assert.equal((p[1] as any).summary, 'tier 2');
});

test('a result with no call_id (an older server) closes the first running call of that name', () => {
  let p: TurnPart[] = [];
  p = applyToolCall(p, { name: 'list_agents', args_summary: '{}' } as any);
  p = applyToolResult(p, { name: 'list_agents', ok: true, summary: '4' } as any);
  assert.equal((p[0] as any).status, 'ok');
});

test('a result for a call never seen is dropped, never invented', () => {
  const p = applyToolResult([{ kind: 'text', text: 'hi' }], { call_id: 'zz', name: 'x', ok: true, summary: '' });
  assert.deepEqual(p, [{ kind: 'text', text: 'hi' }]);
});

test('the reducers never mutate what they are given', () => {
  const before: TurnPart[] = [{ kind: 'text', text: 'a' }];
  const snapshot = JSON.stringify(before);
  applyTextDelta(before, 'b');
  applyToolCall(before, call('c1'));
  assert.equal(JSON.stringify(before), snapshot);
});

test('consecutive tool parts group into one run; text breaks the run', () => {
  let p: TurnPart[] = [];
  p = applyToolCall(p, call('c1'));
  p = applyToolCall(p, call('c2'));
  p = applyTextDelta(p, 'between');
  p = applyToolCall(p, call('c3'));
  const g = groupParts(p);
  assert.deepEqual(g.map((x) => x.kind), ['tools', 'text', 'tools']);
  assert.equal((g[0] as any).tools.length, 2);
});
