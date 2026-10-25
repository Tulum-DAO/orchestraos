/** Run: npx tsx --test src/lib/transcriptMarkdown.test.ts (from api/). Fake secrets are built at run time. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { transcriptMarkdown, lastTurns, type MdItem } from './transcriptMarkdown.js';

const KEY = 'sk-ant-' + 'q'.repeat(40);
const items: MdItem[] = [
  { kind: 'text', role: 'user', text: 'run the tests', ts: '2026-10-16T17:00:00Z' },
  { kind: 'thinking', role: 'assistant', text: `my scratch: the key is ${KEY}` },
  { kind: 'tool_use', role: 'assistant', tool: 'Bash', input: { command: 'npm test -- auth' }, id: 't1' },
  { kind: 'tool_result', role: 'user', text: '42 passed (1.9s)\nlots of output', tool_use_id: 't1' },
  { kind: 'text', role: 'assistant', text: 'All 42 pass.', ts: '2026-10-16T17:00:05Z' },
];
const NOW = new Date('2026-10-16T18:00:00Z');

test('turns, one-line tool calls, header; thinking never', () => {
  const { markdown } = transcriptMarkdown(items, { agent: 'gm', model: 'Opus 5.5', exportedBy: 'shaw', now: NOW });
  assert.match(markdown, /^# gm: conversation/);
  assert.match(markdown, /\*\*Agent:\*\* gm \(Opus 5\.5\)/);
  assert.match(markdown, /### You · 2026-10-16 17:00 UTC\n\nrun the tests/);
  assert.match(markdown, /- ran `Bash` `npm test -- auth` → 42 passed \(1\.9s\)/);
  assert.match(markdown, /### gm · 2026-10-16 17:00 UTC\n\nAll 42 pass\./);
  assert.ok(!markdown.includes('lots of output'), 'tool output left out by default');
  assert.ok(!markdown.includes('my scratch') && !markdown.includes(KEY), 'thinking is never exported');
  assert.match(markdown, /Exported:\*\* 2026-10-16 18:00 UTC by shaw/);
});

test('tools=full includes tool output, redacted, and the header says so (gm)', () => {
  const withSecret = items.map((i) => i.kind === 'tool_result' ? { ...i, text: `ok\nexport GITHUB_TOKEN=${'g'.repeat(20)}` } : i);
  const { markdown, redacted } = transcriptMarkdown(withSecret, { agent: 'gm', tools: 'full', now: NOW });
  assert.match(markdown, /tool output included/);
  assert.match(markdown, /GITHUB_TOKEN=\[redacted: named secret\]/);
  assert.equal(redacted, 1);
  assert.match(markdown, /Secrets removed:\*\* 1 \(1 named secret\)/);
});

test('a secret is redacted BEFORE a long command is shortened (no half key leaks)', () => {
  const cmd = 'curl -s -H "x-api-key: ' + 'z'.repeat(1) + '" ' + 'h'.repeat(30) + ' --data ' + KEY;   // the key straddles the 80-char cut
  const its: MdItem[] = [{ kind: 'text', role: 'user', text: 'go' },
    { kind: 'tool_use', tool: 'Bash', input: { command: cmd }, id: 'x' }];
  const { markdown } = transcriptMarkdown(its, { agent: 'gm', now: NOW });
  assert.ok(!markdown.includes('sk-ant-q'), markdown);
});

test('chat text is redacted too, and the count is honest', () => {
  const its: MdItem[] = [{ kind: 'text', role: 'user', text: `use ${KEY} please` }];
  const { markdown, redacted } = transcriptMarkdown(its, { agent: 'gm', now: NOW });
  assert.ok(markdown.includes('[redacted: Anthropic key]') && !markdown.includes(KEY));
  assert.equal(redacted, 1);
});

test('last N turns starts at the Nth-from-last user message', () => {
  const many: MdItem[] = [];
  for (let i = 0; i < 5; i++) many.push({ kind: 'text', role: 'user', text: `q${i}` }, { kind: 'text', role: 'assistant', text: `a${i}` });
  assert.deepEqual(lastTurns(many, 2).map((i) => i.text), ['q3', 'a3', 'q4', 'a4']);
  assert.equal(lastTurns(many, 50).length, 10);
});
