/**
 * The live step: what an agent is doing right now, from ONE source per seat. A headless seat's supervisor log (a tool
 * from its block start to its result, a held request, the turn's end) and a tmux seat's pane probe (its summary from
 * the transcript's open tool call) both give the same small object; a summary never carries a secret or runs long.
 *
 * Run: npx tsx --test src/services/live-step.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, appendFileSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import { StepState, LiveStep, SUMMARY_MAX, stepSummary, stepFromProbe, openToolUse, headlessStep, dropHeadlessStep } from './live-step.js';

const out = (ev: Record<string, unknown>, wall = '2026-10-31T08:00:00.000Z') => ({ kind: 'out', wall, ev });
const sup = (what: string, extra: Record<string, unknown> = {}, wall = '2026-10-31T08:00:00.000Z') => ({ kind: 'sup', wall, what, ...extra });
const toolUse = (name: string, input: Record<string, unknown>, parent: string | null = null) =>
  out({ type: 'assistant', parent_tool_use_id: parent, message: { id: 'msg_1', content: [{ type: 'tool_use', id: 'tu_1', name, input }] } }, '2026-10-31T08:00:05.000Z');

test('a summary is the part of the input that says what the tool does: short, one line, no secrets', () => {
  assert.equal(stepSummary('Bash', { command: 'npm test\nnpm run lint' }), 'npm test');
  assert.equal(stepSummary('Read', { file_path: '/home/example/project/plan.md' }), 'plan.md');
  assert.equal(stepSummary('Write', { file_path: '/tmp/step2.md', content: 'anything' }), 'step2.md');
  assert.equal(stepSummary('Grep', { pattern: 'TODO' }), 'TODO');
  assert.equal(stepSummary('WebFetch', { url: 'https://example.com/docs/page?token=abc' }), 'example.com/docs/page');
  assert.equal(stepSummary('Task', { description: 'Check the sample report' }), 'Check the sample report');
  assert.equal(stepSummary('SomeTool', { anything: 'value' }), undefined);       // unknown tools: no input shown
  const secret = stepSummary('Bash', { command: 'curl -H "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456" https://example.com' })!;
  assert.ok(!secret.includes('abcdefghijklmnop'), secret);
  assert.ok(secret.includes('[redacted'), secret);
  const long = stepSummary('Bash', { command: `echo ${'x'.repeat(200)}` })!;
  assert.equal(long.length, SUMMARY_MAX);
  assert.ok(long.endsWith('…'));
});

test('a headless turn: thinking, a tool with its summary, back to thinking on its result, idle at the end', () => {
  const s = new StepState();
  s.apply(sup('state', { state: 'working' }));
  assert.equal(s.current()?.kind, 'thinking');
  s.apply(out({ type: 'stream_event', event: { type: 'content_block_start', index: 1, content_block: { type: 'tool_use', name: 'Bash' } } }));
  assert.deepEqual([s.current()?.kind, s.current()?.tool, s.current()?.summary], ['tool', 'Bash', undefined]);
  s.apply(toolUse('Bash', { command: 'npm test' }));
  assert.deepEqual([s.current()?.kind, s.current()?.tool, s.current()?.summary], ['tool', 'Bash', 'npm test']);
  const since = s.current()!.since;
  s.apply(toolUse('Bash', { command: 'npm test' }));                         // the same step goes on: its start stays
  assert.equal(s.current()!.since, since);
  s.apply(out({ type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: 'tu_1' }] } }));
  assert.equal(s.current()?.kind, 'thinking');
  s.apply(out({ type: 'result', subtype: 'success' }));
  assert.equal(s.current()?.kind, 'idle');
});

test('a held question or permission is waiting; a subagent\'s own tools are not the seat\'s; a restart shows none', () => {
  const s = new StepState();
  s.apply(toolUse('Task', { description: 'Check the sample report' }));
  s.apply(toolUse('Read', { file_path: '/x/inner.md' }, 'tu_1'));             // inside the subagent
  assert.deepEqual([s.current()?.tool, s.current()?.summary], ['Task', 'Check the sample report']);
  s.apply(out({ type: 'control_request', request_id: 'r1', request: { subtype: 'can_use_tool', tool_name: 'Bash' } }));
  assert.equal(s.current()?.kind, 'waiting');
  s.apply(sup('state', { state: 'restarting' }));
  assert.equal(s.current(), null);
  s.apply(sup('exit', { code: 0 }));
  assert.equal(s.current()?.kind, 'idle');
});

test('a tmux seat: the probe\'s state, with the summary from the open tool call when it is the same tool', () => {
  const now = Date.parse('2026-10-31T08:00:30Z');
  const open = openToolUse([
    { kind: 'text', role: 'user', text: 'go' },
    { kind: 'tool_use', id: 'a', tool: 'Read', input: { file_path: '/p/plan.md' } },
    { kind: 'tool_result', tool_use_id: 'a' },
    { kind: 'tool_use', id: 'b', tool: 'Bash', input: { command: 'npm test' } },
  ]);
  assert.deepEqual(open, { tool: 'Bash', input: { command: 'npm test' } });
  assert.deepEqual(stepFromProbe({ state: 'working', tool: 'Bash', state_age_s: 10 }, open, now),
    { kind: 'tool', tool: 'Bash', summary: 'npm test', since: '2026-10-31T08:00:20.000Z' });
  assert.deepEqual(stepFromProbe({ state: 'working', tool: 'Read' }, open, now)?.summary, undefined);   // another tool: no summary
  assert.equal(stepFromProbe({ state: 'working' }, null, now)?.kind, 'thinking');
  assert.equal(stepFromProbe({ state: 'waiting_permission' }, null, now)?.kind, 'waiting');
  assert.equal(stepFromProbe({ state: 'idle' }, null, now)?.kind, 'idle');
  assert.equal(stepFromProbe({ state: 'stopped' }, null, now), null);
  assert.equal(openToolUse([{ kind: 'tool_use', id: 'a', tool: 'Bash', input: {} }, { kind: 'tool_result', tool_use_id: 'a' }]), null);
});

test('the log is followed as it grows, shared by everyone who asks for that seat', () => {
  const root = mkdtempSync(join(tmpdir(), 'live-step-'));
  const dir = join(root, 'state', 'headless', 'dev-demo');
  mkdirSync(dir, { recursive: true });
  const log = join(dir, 'events.jsonl');
  writeFileSync(log, JSON.stringify(sup('state', { state: 'working' })) + '\n');
  dropHeadlessStep();
  assert.equal(headlessStep('dev-demo', root)?.kind, 'thinking');
  appendFileSync(log, JSON.stringify(toolUse('Read', { file_path: '/p/plan.md' })) + '\n');
  assert.deepEqual([headlessStep('dev-demo', root)?.tool, headlessStep('dev-demo', root)?.summary], ['Read', 'plan.md']);
  const own = new LiveStep(log);
  assert.equal(own.read()?.summary, 'plan.md');                                // a fresh reader catches up from the log
  dropHeadlessStep();
});
