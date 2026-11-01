import { test } from 'node:test';
import assert from 'node:assert/strict';
import { stepFrom, stepText, stepElapsed } from './liveStep.ts';

const s = (over: Record<string, unknown>) => stepFrom({ agent_id: 'dev-demo', since: '2026-10-31T08:00:00Z', ...over })!;

test('the line says what the agent is doing, in words', () => {
  assert.equal(stepText(s({ kind: 'tool', tool: 'Bash', summary: 'npm test' })), 'Running a shell command: npm test');
  assert.equal(stepText(s({ kind: 'tool', tool: 'Read', summary: 'plan.md' })), 'Reading plan.md');
  assert.equal(stepText(s({ kind: 'tool', tool: 'Write', summary: 'step2.md' })), 'Writing step2.md');
  assert.equal(stepText(s({ kind: 'tool', tool: 'Read' })), 'Reading a file');
  assert.equal(stepText(s({ kind: 'tool', tool: 'Task', summary: 'Check the sample report' })), 'Running a subagent: Check the sample report');
  assert.equal(stepText(s({ kind: 'tool', tool: 'mcp__example__look_up' })), 'Using look up');
  assert.equal(stepText(s({ kind: 'thinking' })), 'Thinking…');
  assert.equal(stepText(s({ kind: 'waiting' })), 'Waiting for your answer');
  assert.equal(stepText(s({ kind: 'idle' })), 'Idle');
});

test('a frame that is no step (kind null, or not one this page reads) shows no line', () => {
  assert.equal(stepFrom({ agent_id: 'dev-demo', kind: null }), null);
  assert.equal(stepFrom({ agent_id: 'dev-demo', kind: 'other' }), null);
  assert.equal(stepFrom(null), null);
  assert.deepEqual(stepFrom({ agent_id: 'dev-demo', kind: 'tool', tool: 'Bash', summary: '  ' }), { agent_id: 'dev-demo', kind: 'tool', tool: 'Bash' });
});

test('how long the step has been going', () => {
  const now = Date.parse('2026-10-31T08:01:05Z');
  assert.equal(stepElapsed(s({ kind: 'thinking' }), now), '1m 5s');
  assert.equal(stepElapsed(s({ kind: 'thinking', since: '2026-10-31T08:01:00Z' }), now), '5s');
  assert.equal(stepElapsed(stepFrom({ agent_id: 'dev-demo', kind: 'idle' })!, now), '');
});
