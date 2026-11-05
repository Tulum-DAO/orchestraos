import { test } from 'node:test';
import assert from 'node:assert/strict';
import { threadFromTranscript, threadFromArturo, threadMessages, isoTime, type ThreadMessage, type ThreadToolRun, type ThreadResult } from './threadKit/thread.ts';
import { hydrateTurns } from './arturoResume.ts';
import { applyTextDelta, applyToolCall, applyToolResult } from './turnParts.ts';
import type { ChatItem } from './transcript.ts';
import type { ThreadTurn } from './arturoThreads.ts';

// Fixtures shaped on real records (an agent's transcript items, an Arturo thread's stored turns), names neutral.

let clock = Date.parse('2026-10-30T01:00:00Z');
const ts = () => new Date(clock += 15_000).toISOString();

const AGENT_ITEMS = [
  { kind: 'text', role: 'user', text: 'You are dev-demo. Read /tmp/agent-init-dev-demo.md and follow it.', ts: ts(), uuid: 'boot' },
  { kind: 'text', role: 'user', text: '[IMAGE: /srv/demo/state/uploads/sample-1.png] Check the sample page.', ts: ts(), uuid: 'u1' },
  { kind: 'thinking', role: 'assistant', text: 'Read the page first.', ts: ts(), uuid: 'th1' },
  { kind: 'tool_use', role: 'assistant', tool: 'Read', id: 'toolu_r1', input: { file_path: '/home/demo/sample/index.html' }, ts: ts(), uuid: 'tu1' },
  { kind: 'tool_result', role: 'user', tool_use_id: 'toolu_r1', text: '120 lines', ts: ts(), uuid: 'tr1' },
  { kind: 'thinking', role: 'assistant', text: 'Now the count.', ts: ts(), uuid: 'th2' },
  { kind: 'tool_use', role: 'assistant', tool: 'Bash', id: 'toolu_b1', input: { command: 'grep -c h1 index.html | sort', description: 'Count headings' }, ts: ts(), uuid: 'tu2' },
  { kind: 'tool_result', role: 'user', tool_use_id: 'toolu_b1', text: '6', ts: ts(), uuid: 'tr2' },
  { kind: 'text', role: 'assistant', text: '| Page | H1s |\n|---|---|\n| home | 6 |', ts: ts(), uuid: 'a1' },
  { kind: 'text', role: 'user', text: 'And the slow check?', ts: ts(), uuid: 'u2' },
  { kind: 'tool_use', role: 'assistant', tool: 'Bash', id: 'toolu_bg', input: { command: './slow-sample.sh', description: 'Sample slow check', run_in_background: true }, ts: ts(), uuid: 'tu3' },
  { kind: 'tool_result', role: 'user', tool_use_id: 'toolu_bg', text: 'Command running in background with ID: bg1.', ts: ts(), uuid: 'tr3' },
  { kind: 'text', role: 'user', machine: 'task', mid_turn: true, ts: ts(), uuid: 'n1',
    text: '<task-notification>\n<task-id>bg1</task-id>\n<tool-use-id>toolu_bg</tool-use-id>\n<status>failed</status>\n<summary>Background command "Sample slow check" failed with exit code 3</summary>\n</task-notification>' },
  { kind: 'text', role: 'assistant', text: 'The slow check failed with exit code 3.', ts: ts(), uuid: 'a2' },
  { kind: 'queued_batch', role: 'user', count: 1, entries: [{ agent: 'pm-demo', sent_ts: ts(), body: 'Sample note.' }], ts: ts(), key: 'qb1' },
] as unknown as ChatItem[];

test('an agent transcript maps into the thread model: messages, thinking, a tool run, a result, a batch', () => {
  const th = threadFromTranscript(AGENT_ITEMS, 'dev-demo');
  assert.equal(th.source, 'agent');
  assert.deepEqual(th.items.map((x) => x.kind),
    ['message', 'message', 'thinking', 'tools', 'message', 'message', 'tools', 'result', 'message', 'batch']);
  const [boot, ask] = th.items as ThreadMessage[];
  assert.equal(boot.system, true);
  assert.equal(ask.role, 'user');
  assert.equal(ask.text, 'Check the sample page.');
  assert.deepEqual(ask.attachments, [{ kind: 'image', name: 'sample-1.png', url: '/uploads/sample-1.png' }]);
  // Two calls with thinking between them: one run, in order.
  const run = th.items[3] as ThreadToolRun;
  assert.deepEqual(run.steps.map((s) => s.kind), ['call', 'thinking', 'call']);
  assert.deepEqual(run.calls.map((c) => [c.name, c.status]), [['Read', 'ok'], ['Bash', 'ok']]);
  assert.equal(run.calls[1].chip, 'grep -c h1 index.html | sort');
  // A reply's copy takes its words as written.
  const reply = th.items[4] as ThreadMessage;
  assert.equal(reply.role, 'assistant');
  assert.equal(reply.copyText, '| Page | H1s |\n|---|---|\n| home | 6 |');
  // The failed background job: open, with its reason.
  const res = th.items[7] as ThreadResult;
  assert.equal(res.view, 'open');
  assert.equal(res.results[0].outcome, 'failed');
  assert.equal(res.results[0].error, 'Exit code 3');
  assert.ok(th.items.every((x) => !x.at || !Number.isNaN(Date.parse(x.at))));
});

const AT = 1792854000;
const ARTURO_TURNS: ThreadTurn[] = [
  { role: 'user', content: '[Context: route=/projects]\nCompare the sample sites.', ts: AT },
  { role: 'assistant', content: 'Started two research jobs.', ts: AT + 4 },
  { role: 'assistant', content: 'Grading the Sample website: done.\n\nGrade: B.', ts: AT + 300,
    meta: { kind: 'job_result', job_id: 'job_demo1', job_kind: 'research', title: 'Grading the Sample website', status: 'done',
      steps: [{ call_id: 'c1', name: 'WebFetch', args_summary: 'https://example.com/', ok: true, summary: 'fetched' }] } },
  { role: 'assistant', content: "Checking the Example blog: didn't finish.", ts: AT + 320,
    meta: { kind: 'job_result', job_id: 'job_demo2', job_kind: 'research', title: 'Checking the Example blog', status: 'failed', steps: [] } },
  { role: 'assistant', content: 'Which one should I look at first?', ts: AT + 330,
    meta: { kind: 'card', card_id: 'card_demo', choices: { options: ['Sample', 'Example'], multi: false, purpose: 'other' } } },
  { role: 'user', content: 'Sample', ts: AT + 340 },
  { role: 'assistant', content: '| Site | Grade |\n|---|---|\n| Sample | B |', ts: AT + 350 },
];

test('an Arturo thread maps into the same model: messages, a stack of results, a card and its answer', () => {
  const th = threadFromArturo(hydrateTurns(ARTURO_TURNS), 'Example');
  assert.equal(th.source, 'arturo');
  assert.deepEqual(th.items.map((x) => x.kind), ['message', 'message', 'result', 'message', 'message', 'message']);
  const first = th.items[0] as ThreadMessage;
  assert.equal(first.text, 'Compare the sample sites.', 'the page-context line is not the operator\'s words');
  assert.equal(first.at, isoTime(AT));
  const stack = th.items[2] as ThreadResult;
  assert.deepEqual(stack.results.map((r) => [r.title, r.outcome, r.by]),
    [['Grading the Sample website', 'done', 'Example'], ['Checking the Example blog', 'failed', 'Example']]);
  const card = th.items[3] as ThreadMessage;
  assert.deepEqual(card.choices?.options, ['Sample', 'Example']);
  assert.equal(card.answered, 'Sample');
  assert.equal((th.items[4] as ThreadMessage).cardEcho, true);
});

test('a streamed Arturo reply keeps its text and tool runs in order; Copy takes only the words', () => {
  let parts = applyTextDelta([], 'Checking.');
  parts = applyToolCall(parts, { call_id: 'c1', name: 'WebFetch', args_summary: 'https://example.com/' });
  parts = applyToolResult(parts, { call_id: 'c1', name: 'WebFetch', ok: true, summary: 'fetched' });
  parts = applyTextDelta(parts, 'Done.');
  const th = threadFromArturo([{ role: 'arturo', text: 'Checking.Done.', parts, streaming: true, id: 7 }]);
  const m = th.items[0] as ThreadMessage;
  assert.equal(m.key, 't7');
  assert.equal(m.streaming, true);
  assert.deepEqual(m.parts?.map((p) => p.kind), ['text', 'tools', 'text']);
  assert.equal(m.parts?.[1].kind === 'tools' && m.parts[1].calls[0].status, 'ok');
  assert.equal(m.copyText, 'Checking.\n\nDone.');
});

test('the same exchange from either source reads as the same messages', () => {
  const agent = threadFromTranscript([
    { kind: 'text', role: 'user', text: 'Hello?', ts: isoTime(AT), uuid: 'x1' },
    { kind: 'text', role: 'assistant', text: 'Here. **Ready.**', ts: isoTime(AT + 2), uuid: 'x2' },
  ] as unknown as ChatItem[]);
  const arturo = threadFromArturo(hydrateTurns([
    { role: 'user', content: 'Hello?', ts: AT },
    { role: 'assistant', content: 'Here. **Ready.**', ts: AT + 2 },
  ]));
  const shape = (ms: ThreadMessage[]) => ms.map((m) => ({ role: m.role, text: m.text, at: m.at, copyText: m.copyText }));
  assert.deepEqual(shape(threadMessages(agent)), shape(threadMessages(arturo)));
});
