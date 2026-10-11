import assert from 'node:assert/strict';
import test from 'node:test';
import { AGENT_CARD_TITLE_CLASS } from './agentCardTitle';

test('agent card titles use a tight line box for vertical alignment', () => {
  assert.match(AGENT_CARD_TITLE_CLASS, /\bleading-none\b/);
});
