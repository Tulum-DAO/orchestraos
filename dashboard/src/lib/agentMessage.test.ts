import { test } from 'node:test';
import assert from 'node:assert/strict';
import { senderAgent, senderName, type AgentLike } from './agentMessage.ts';

const agents: AgentLike[] = [
  { id: 'pm-demo', display_name: 'pm', full_name: 'Example · pm', project: { slug: 'example' } },
  { id: 'dev-demo-g2', tmux_session: 'dev-demo-g2', display_name: 'dev', full_name: 'Example · dev', project: { slug: 'example' } },
  { id: 'qa-sample', display_name: 'qa', full_name: 'Sample · qa', project: { slug: 'sample' } },
  { id: 'gm', display_name: 'gm', full_name: 'gm', project: null },
];

test('the sender is found by id, by session, or by its seat', () => {
  assert.equal(senderAgent(agents, 'pm-demo')?.id, 'pm-demo');
  assert.equal(senderAgent(agents, 'dev-demo')?.id, 'dev-demo-g2');
  assert.equal(senderAgent(agents, 'approval-loop'), undefined);
});

test('short name inside the same project, full name anywhere else, raw id for a service', () => {
  const viewer = agents[0];
  assert.equal(senderName(agents[1], 'dev-demo', viewer), 'dev');
  assert.equal(senderName(agents[2], 'qa-sample', viewer), 'Sample · qa');
  assert.equal(senderName(agents[1], 'dev-demo', agents[3]), 'Example · dev');
  assert.equal(senderName(undefined, 'approval-loop', viewer), 'approval-loop');
});

