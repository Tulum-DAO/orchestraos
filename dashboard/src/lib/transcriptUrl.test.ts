/**
 * One URL builder for the seat transcript and a subagent's own transcript (agent-tree drill-in).
 * The seat URLs must stay byte-for-byte what the chat fetched before; the subagent id is validated
 * with the API's own rule (SAFE_SUBAGENT_ID in api/src/routes/chat-transcript.ts).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { transcriptUrl, treeUrl } from './transcriptUrl';

test('seat URLs are unchanged byte for byte', () => {
  assert.equal(transcriptUrl('gm', { limit: 150 }), '/api/agents/gm/transcript?limit=150');
  assert.equal(transcriptUrl('pm-demo', { limit: 150, stream: true }), '/api/agents/pm-demo/transcript/stream?limit=150');
  assert.equal(transcriptUrl('a b', { limit: 120 }), '/api/agents/a%20b/transcript?limit=120');
});

test('subagent URLs carry both ids', () => {
  assert.equal(transcriptUrl('pm-demo', { limit: 150, subagentId: 'a0000000000000abc' }),
    '/api/agents/pm-demo/subagents/a0000000000000abc/transcript?limit=150');
  assert.equal(transcriptUrl('pm-demo', { limit: 150, subagentId: 'a0000000000000abc', stream: true }),
    '/api/agents/pm-demo/subagents/a0000000000000abc/transcript/stream?limit=150');
  assert.equal(transcriptUrl('a b', { limit: 5, subagentId: 'abcdef' }), '/api/agents/a%20b/subagents/abcdef/transcript?limit=5');
});

test('an unsafe subagent id throws', () => {
  for (const bad of ['../etc', 'ABCDEF', 'abc', 'a'.repeat(41), 'abcdef/x', '']) {
    assert.throws(() => transcriptUrl('gm', { limit: 1, subagentId: bad }), /subagent id/, bad);
  }
});

test('tree URLs: snapshot, stream, and ?all=1 only when asked', () => {
  assert.equal(treeUrl('pm-demo'), '/api/agents/pm-demo/tree');
  assert.equal(treeUrl('pm-demo', { stream: true }), '/api/agents/pm-demo/tree/stream');
  assert.equal(treeUrl('pm-demo', { all: true }), '/api/agents/pm-demo/tree?all=1');
  assert.equal(treeUrl('pm-demo', { stream: true, all: true }), '/api/agents/pm-demo/tree/stream?all=1');
});
