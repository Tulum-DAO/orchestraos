import { test } from 'node:test';
import assert from 'node:assert/strict';
import { agentCallBlock, agentCallsFrom, calleeName, callPartyId, fetchAgentCallsOn } from './agentCallsOn.ts';

const answer = (status: number, body: unknown) => (async () => new Response(JSON.stringify(body), { status })) as typeof fetch;

test('agent_calls: only true means the call reaches the agent; false, missing (older API) or unreadable is Arturo', async () => {
  assert.equal(agentCallsFrom({ ok: true, settings: { agent_calls: true, show_compaction: true } }), true);
  assert.equal(agentCallsFrom({ agent_calls: true }), true);
  // In Dev the call is still the agent's (it says to tap Back to chat): a separate in_dev never changes the answer.
  assert.equal(agentCallsFrom({ ok: true, settings: { agent_calls: true, in_dev: true } }), true);
  assert.equal(agentCallsFrom({ ok: true, settings: { agent_calls: false, in_dev: true } }), false);
  assert.equal(agentCallsFrom({ ok: true, settings: { agent_calls: false } }), false);
  assert.equal(agentCallsFrom({ ok: true, settings: { show_compaction: true, compact_at: 65 } }), false);
  assert.equal(agentCallsFrom({ ok: true, settings: { agent_calls: 'yes' } }), false);
  assert.equal(agentCallsFrom(null), false);
  let url = '';
  assert.equal(await fetchAgentCallsOn('dev-demo', (async (u: string) => { url = u; return new Response(JSON.stringify({ ok: true, settings: { agent_calls: true } })); }) as typeof fetch), true);
  assert.equal(url, '/api/agents/dev-demo/settings');
  assert.equal(await fetchAgentCallsOn('dev-demo', answer(200, { ok: true, settings: { compact_at: 65 } })), false);
  assert.equal(await fetchAgentCallsOn('dev-demo', answer(404, { error: 'nf' })), false);
  assert.equal(await fetchAgentCallsOn('dev-demo', (async () => { throw new Error('offline'); }) as typeof fetch), false);
});

test('the button and every call surface name who answers: the agent when on, Arturo when off', () => {
  assert.equal(calleeName(true, 'dev-demo', 'Sample'), 'dev-demo');
  assert.equal(calleeName(false, 'dev-demo', 'Sample'), 'Sample');
  assert.equal(callPartyId({ agentId: 'dev-demo', party: 'agent' }, 'arturo'), 'dev-demo');
  assert.equal(callPartyId({ agentId: 'dev-demo', party: 'arturo' }, 'arturo'), 'arturo');
  assert.equal(callPartyId({ agentId: 'dev-demo' }, 'arturo'), 'arturo', 'unmarked: Arturo, never a claim it reached the agent');
});

test('a call from an agent composer is that agent or no call: never Arturo', () => {
  assert.equal(agentCallBlock(true, 'dev-demo'), null);
  assert.equal(agentCallBlock(false, 'gm'), "gm can't take calls yet");
});
