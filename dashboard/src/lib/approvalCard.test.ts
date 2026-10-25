import test from 'node:test';
import assert from 'node:assert/strict';
import { approvalCardBody, approvalCardAgent } from './approvalCard';

test('body is the summary', () => {
  assert.equal(approvalCardBody({ title: 'Q?', description: '**Do the signups first.**' }), '**Do the signups first.**');
});

test('body is empty when missing or a repeat of the title', () => {
  assert.equal(approvalCardBody({ title: 'Q?', description: '' }), '');
  assert.equal(approvalCardBody({ title: 'Q?', description: null }), '');
  assert.equal(approvalCardBody({ title: 'Q?', description: ' Q? ' }), '');
});

test('agent is the asking seat, empty when unnamed', () => {
  assert.equal(approvalCardAgent({ agent_id: 'pm-job-search' }), 'pm-job-search');
  assert.equal(approvalCardAgent({ agent_id: null }), '');
  assert.equal(approvalCardAgent({}), '');
});
