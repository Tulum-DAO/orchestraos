// `model` on assistant transcript items (ios-watch-dev, Shaw-approved 2026-10-22): clients draw
// "Model changed · Fable 5.1 -> Opus 5.5" where it changes. Run: npx tsx --test src/routes/chat-transcript.model.test.ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeTranscript, parseCodexRollout } from './chat-transcript.js';

const claude = (role: 'user' | 'assistant', content: any, model?: string, ts = '2026-10-22T05:00:00Z') =>
  JSON.stringify({ type: role, uuid: `u-${Math.random()}`, timestamp: ts,
    message: { role, content, ...(model ? { model } : {}) } });

test('every assistant item carries the model that wrote it; user items never', () => {
  const lines = [
    claude('user', 'hi'),
    claude('assistant', [{ type: 'thinking', thinking: 'hm' }, { type: 'text', text: 'hello' },
      { type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } }], 'claude-fable-5-1'),
    claude('user', [{ type: 'tool_result', tool_use_id: 't1', content: 'a' }]),
    claude('assistant', 'switched', 'claude-opus-5-5'),
  ];
  const { items } = normalizeTranscript(lines, 'gm', 's', 150) as any;
  const asst = items.filter((i: any) => i.role === 'assistant');
  assert.ok(asst.length >= 3);
  assert.deepEqual([...new Set(asst.map((i: any) => i.model))], ['claude-fable-5-1', 'claude-opus-5-5']);
  assert.ok(items.filter((i: any) => i.role === 'user').every((i: any) => i.model === undefined));
});

test('Claude Code\'s <synthetic> placeholder is not a model', () => {
  const { items } = normalizeTranscript([claude('user', 'x'), claude('assistant', 'harness note', '<synthetic>')], 'gm', 's', 150) as any;
  assert.ok(items.every((i: any) => i.model === undefined));
});

test('Codex items take the model of the turn_context they ran under', () => {
  const rec = (ordinal: number, type: string, payload: any) => JSON.stringify({ ordinal, type, timestamp: '2026-10-22T05:00:00Z', payload });
  const lines = [
    rec(1, 'turn_context', { model: 'gpt-5.6-luna' }),
    rec(2, 'response_item', { type: 'message', role: 'user', content: [{ type: 'input_text', text: 'go' }] }),
    rec(3, 'response_item', { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'one' }] }),
    rec(4, 'turn_context', { model: 'gpt-5.6-terra' }),
    rec(5, 'response_item', { type: 'function_call', name: 'shell', arguments: '{"cmd":"ls"}', call_id: 'c1' }),
    rec(6, 'response_item', { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'two' }] }),
  ];
  const items = parseCodexRollout(lines);
  assert.deepEqual(items.filter((i: any) => i.role === 'assistant').map((i: any) => [i.kind, i.model]),
    [['text', 'gpt-5.6-luna'], ['tool_use', 'gpt-5.6-terra'], ['text', 'gpt-5.6-terra']]);
  assert.equal(items.find((i: any) => i.role === 'user').model, undefined);
});
