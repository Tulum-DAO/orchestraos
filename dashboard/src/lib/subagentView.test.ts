/**
 * The subagent drill-in (agent-tree) on this dashboard: which skin it is drawn in, and whether the agent page shows
 * the terminal, the seat's chat or a subagent's transcript.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { subagentSkin, pageBody } from './subagentView';

test('a subagent is drawn in the agent\'s own chat skin; Dev (a terminal) falls back to Bubbles', () => {
  assert.equal(subagentSkin('modern'), 'modern');
  assert.equal(subagentSkin('bubbles'), 'bubbles');
  assert.equal(subagentSkin('dev'), 'bubbles');
});

test('pageBody: the terminal only for the seat itself in Dev; a subagent is always its read-only transcript', () => {
  assert.equal(pageBody({ mode: 'dev', readOnly: false }), 'terminal');
  assert.equal(pageBody({ mode: 'chat', readOnly: false }), 'chat');
  assert.equal(pageBody({ mode: 'dev', readOnly: true }), 'subagent');
  assert.equal(pageBody({ mode: 'chat', readOnly: true }), 'subagent');
});
