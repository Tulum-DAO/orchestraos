// `usage` on agent transcript items (Shaw 2026-10-22: "not only which model wrote it, but how many
// tokens were spent", "we are employing the OpenTelemetry methodology"). Keys follow the OTel GenAI
// semantic conventions (gen_ai.usage.*). Run: npx tsx --test src/routes/chat-transcript.usage.test.ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeTranscript, parseCodexRollout } from './chat-transcript.js';

const U1 = { input_tokens: 2, cache_creation_input_tokens: 300, cache_read_input_tokens: 5000, output_tokens: 71 };
const U2 = { input_tokens: 10, cache_creation_input_tokens: 0, cache_read_input_tokens: 6000, output_tokens: 9 };
let n = 0;
const entry = (role: 'user' | 'assistant', content: any, msg: any = {}) =>
  JSON.stringify({ type: role, uuid: `u-${n++}`, timestamp: '2026-10-22T05:00:00Z',
    message: { role, content, ...msg } });

// Claude Code writes each block of ONE API reply as its own line, every line repeating the same usage.
const reply = (id: string, usage: any, blocks: any[]) =>
  blocks.map((b) => entry('assistant', [b], { id, usage, model: 'claude-opus-5-5' }));

test('a reply split over several lines carries its usage once, on its first item, in OTel terms', () => {
  const lines = [
    entry('user', 'go'),
    ...reply('msg_1', U1, [{ type: 'thinking', thinking: 'hm' }, { type: 'text', text: 'one' },
      { type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } }]),
    entry('user', [{ type: 'tool_result', tool_use_id: 't1', content: 'a' }]),
    ...reply('msg_2', U2, [{ type: 'text', text: 'two' }]),
  ];
  const { items } = normalizeTranscript(lines, 'gm', 's', 150) as any;
  const withUsage = items.filter((i: any) => i.usage);
  assert.deepEqual(withUsage.map((i: any) => [i.kind, i.text]), [['thinking', 'hm'], ['text', 'two']]);
  // gen_ai.usage.input_tokens is ALL input, cached included; Claude's own input_tokens is not.
  assert.deepEqual(withUsage[0].usage, { input_tokens: 5302, output_tokens: 71,
    cache_read_input_tokens: 5000, cache_creation_input_tokens: 300 });
  assert.ok(items.filter((i: any) => i.role === 'user').every((i: any) => i.usage === undefined));
});

test('summing usage over items equals summing it over replies (never once per line)', () => {
  const lines = [entry('user', 'go'),
    ...reply('msg_a', U1, [{ type: 'text', text: 'a' }, { type: 'text', text: 'b' }, { type: 'text', text: 'c' }]),
    ...reply('msg_b', U2, [{ type: 'text', text: 'd' }, { type: 'text', text: 'e' }])];
  const { items } = normalizeTranscript(lines, 'gm', 's', 150) as any;
  const out = items.reduce((s: number, i: any) => s + (i.usage?.output_tokens || 0), 0);
  assert.equal(out, U1.output_tokens + U2.output_tokens);
});

test('a reply whose first line draws nothing gives its usage to the first line that does', () => {
  const lines = [entry('user', 'go'), ...reply('msg_1', U1, [{ type: 'text', text: '' }, { type: 'text', text: 'shown' }])];
  const { items } = normalizeTranscript(lines, 'gm', 's', 150) as any;
  assert.equal(items.find((i: any) => i.usage)?.text, 'shown');
});

test('a tool call that opens a reply keeps the usage through enrichment', () => {
  const lines = [entry('user', 'go'), ...reply('msg_1', U1, [{ type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } }])];
  const { items } = normalizeTranscript(lines, 'gm', 's', 150) as any;
  assert.equal(items.find((i: any) => i.kind === 'tool_use').usage.output_tokens, 71);
});

test('no reply id, or no output count: no usage (never a guess, never counted twice)', () => {
  const lines = [entry('user', 'go'),
    entry('assistant', [{ type: 'text', text: 'x' }], { usage: U1 }),
    entry('assistant', [{ type: 'text', text: 'y' }], { id: 'msg_9', usage: { input_tokens: 3 } })];
  const { items } = normalizeTranscript(lines, 'gm', 's', 150) as any;
  assert.ok(items.every((i: any) => i.usage === undefined));
});

const rec = (ordinal: number, type: string, payload: any) =>
  JSON.stringify({ ordinal, type, timestamp: '2026-10-22T05:00:00Z', payload });
const say = (ordinal: number, text: string) =>
  rec(ordinal, 'response_item', { type: 'message', role: 'assistant', content: [{ type: 'output_text', text }] });
const count = (ordinal: number, last: any, totalTokens: number) => rec(ordinal, 'event_msg', { type: 'token_count',
  info: { total_token_usage: { total_tokens: totalTokens }, last_token_usage: last } });

test('a Codex response carries its token_count on its last agent item; Codex input already includes cached', () => {
  const last = { input_tokens: 23124, cached_input_tokens: 11008, cache_write_input_tokens: 0, output_tokens: 168, total_tokens: 23292 };
  const items = parseCodexRollout([
    rec(1, 'response_item', { type: 'message', role: 'user', content: [{ type: 'input_text', text: 'go' }] }),
    rec(2, 'response_item', { type: 'function_call', name: 'shell', arguments: '{}', call_id: 'c1' }),
    say(3, 'done'),
    count(4, last, 23292),
  ]);
  assert.equal(items.find((i: any) => i.kind === 'tool_use').usage, undefined);
  assert.deepEqual(items.find((i: any) => i.text === 'done').usage, { input_tokens: 23124, output_tokens: 168,
    cache_read_input_tokens: 11008, cache_creation_input_tokens: 0 });
});

test('a repeated Codex token_count (same running total) is not counted again; one with nothing new carries to the next item', () => {
  const last = { input_tokens: 100, cached_input_tokens: 0, output_tokens: 5, total_tokens: 105 };
  const items = parseCodexRollout([
    say(1, 'one'), count(2, last, 105), count(3, last, 105),     // a rate-limit-only repeat
    count(4, { input_tokens: 7, output_tokens: 1, total_tokens: 8 }, 113),   // no new item: carried
    say(5, 'two'),
  ]);
  assert.deepEqual(items.map((i: any) => i.usage?.output_tokens), [5, 1]);
});

test('an old token_count re-sent after a new agent item does not take that item; its own count does', () => {
  const a = { input_tokens: 100, output_tokens: 5, total_tokens: 105 };
  const b = { input_tokens: 200, output_tokens: 9, total_tokens: 209 };
  const items = parseCodexRollout([
    say(1, 'one'), count(2, a, 105),
    say(3, 'two'), count(4, a, 105),                          // the previous count again (rate limits)
    count(5, b, 314),                                         // then the count for 'two'
  ]);
  assert.deepEqual(items.map((i: any) => i.usage?.output_tokens), [5, 9]);
});

test('a 5-line Claude reply contributes its usage exactly once (gm)', () => {
  const lines = [entry('user', 'go'), ...reply('msg_5', U1, [
    { type: 'thinking', thinking: 'a' }, { type: 'text', text: 'b' },
    { type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } },
    { type: 'tool_use', id: 't2', name: 'Read', input: { file_path: '/x' } }, { type: 'text', text: 'c' }])];
  const { items } = normalizeTranscript(lines, 'gm', 's', 150) as any;
  assert.equal(items.filter((i: any) => i.role === 'assistant').length, 5);
  assert.equal(items.filter((i: any) => i.usage).length, 1);
  assert.equal(items.reduce((s: number, i: any) => s + (i.usage?.output_tokens || 0), 0), U1.output_tokens);
});

test('a Codex call that drew nothing carries its tokens to the next agent item (the sum stays the total)', () => {
  const reasoning = (o: number) => rec(o, 'response_item', { type: 'reasoning', summary: [], encrypted_content: 'x' });
  const items = parseCodexRollout([
    say(1, 'one'), count(2, { input_tokens: 100, cached_input_tokens: 40, output_tokens: 5 }, 105),
    reasoning(3), count(4, { input_tokens: 120, cached_input_tokens: 100, output_tokens: 30 }, 255),   // drew nothing
    say(5, 'two'), count(6, { input_tokens: 200, cached_input_tokens: 150, output_tokens: 9 }, 464),
  ]);
  assert.deepEqual(items.map((i: any) => i.usage), [
    { input_tokens: 100, output_tokens: 5, cache_read_input_tokens: 40 },
    { input_tokens: 320, output_tokens: 39, cache_read_input_tokens: 250 },
  ]);
});

test('a Claude reply that drew nothing carries its tokens to the next reply\'s first item', () => {
  const lines = [entry('user', 'go'),
    ...reply('msg_empty', U2, [{ type: 'text', text: '' }]),
    ...reply('msg_next', U1, [{ type: 'text', text: 'shown' }])];
  const { items } = normalizeTranscript(lines, 'gm', 's', 150) as any;
  const u = items.find((i: any) => i.usage).usage;
  assert.equal(u.output_tokens, U1.output_tokens + U2.output_tokens);
  assert.equal(u.cache_read_input_tokens, U1.cache_read_input_tokens + U2.cache_read_input_tokens);
});

test('carried Claude tokens go on the next drawn agent item even when that reply has no usage of its own', () => {
  const lines = [entry('user', 'go'),
    ...reply('msg_empty', U2, [{ type: 'text', text: '' }]),
    entry('assistant', [{ type: 'text', text: 'shown' }], { id: 'msg_nousage', model: 'claude-opus-5-5' })];
  const { items } = normalizeTranscript(lines, 'gm', 's', 150) as any;
  assert.equal(items.find((i: any) => i.text === 'shown')?.usage?.output_tokens, U2.output_tokens);
});

test('carried Codex tokens go on the next agent item even when no later count arrives', () => {
  const reasoning = (o: number) => rec(o, 'response_item', { type: 'reasoning', summary: [], encrypted_content: 'x' });
  const items = parseCodexRollout([
    reasoning(1), count(2, { input_tokens: 120, output_tokens: 30 }, 150),
    say(3, 'two'),
  ]);
  assert.equal(items[0].usage?.output_tokens, 30);
});

test('a carried Codex amount and the item\'s own later count add up on the same item', () => {
  const reasoning = (o: number) => rec(o, 'response_item', { type: 'reasoning', summary: [], encrypted_content: 'x' });
  const items = parseCodexRollout([
    reasoning(1), count(2, { input_tokens: 120, output_tokens: 30 }, 150),
    say(3, 'two'), count(4, { input_tokens: 200, output_tokens: 9 }, 359),
  ]);
  assert.deepEqual(items.map((i: any) => i.usage?.output_tokens), [39]);
});
