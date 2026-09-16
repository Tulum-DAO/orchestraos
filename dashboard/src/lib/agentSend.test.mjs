/**
 * B1 client tests — dashboard/src/lib/agentSend.ts (Agent Page v1, DEC-1789508247033721).
 * Mocks global fetch (the only boundary this module has) — pure logic + a
 * fake network, same style as menuAnswer.test.mjs.
 *   node --experimental-strip-types dashboard/src/lib/agentSend.test.mjs
 */
import assert from 'node:assert';
import {
  sendToAgent,
  isDelivered,
  isQueued,
  isHeld,
  isComposerHold,
  describeSendState,
} from './agentSend.ts';

function mockFetch(status, jsonBody) {
  return async (url, init) => {
    mockFetch.lastUrl = url;
    mockFetch.lastInit = init;
    return {
      status,
      json: async () => jsonBody,
    };
  };
}

// --- delivered ---------------------------------------------------------
{
  globalThis.fetch = mockFetch(200, { state: 'delivered', session: 'gm', attempts: 1 });
  const result = await sendToAgent('gm', { text: 'hi' });
  assert.strictEqual(result.httpStatus, 200);
  assert.strictEqual(result.state, 'delivered');
  assert.strictEqual(isDelivered(result), true);
  assert.strictEqual(isQueued(result), false);
  assert.strictEqual(isHeld(result), false);
  console.log('PASS: delivered result classified correctly');
}

// --- always advertises send-states capability ---------------------------
{
  globalThis.fetch = mockFetch(200, { state: 'delivered' });
  await sendToAgent('gm', { text: 'hi' });
  const { lastInit, lastUrl } = mockFetch;
  assert.strictEqual(lastUrl, '/api/agents/gm/send');
  assert.strictEqual(lastInit.headers['X-Client-Capabilities'], 'send-states');
  const body = JSON.parse(lastInit.body);
  assert.deepStrictEqual(body.client_caps, ['send-states']);
  assert.strictEqual(body.text, 'hi');
  console.log('PASS: request advertises send-states capability (header + body)');
}

// --- queued --------------------------------------------------------------
{
  globalThis.fetch = mockFetch(200, { state: 'queued', message_id: 'msg_1', reason: 'agent busy' });
  const result = await sendToAgent('gm', { text: 'hi' });
  assert.strictEqual(isQueued(result), true);
  assert.strictEqual(describeSendState(result), 'agent busy');
  console.log('PASS: queued result classified + described');
}

// --- held ------------------------------------------------------------------
{
  globalThis.fetch = mockFetch(200, {
    state: 'held', message_id: 'msg_2',
    reason: 'agent pane is showing a menu/permission prompt',
  });
  const result = await sendToAgent('gm', { text: 'hi' });
  assert.strictEqual(isHeld(result), true);
  assert.match(describeSendState(result), /menu\/permission prompt/);
  console.log('PASS: held result classified + described');
}

// --- 409 composer-hold (D4): payload echoed back for a force retry --------
{
  const payload = { text: 'my message', attachments: undefined, client_caps: ['send-states'] };
  globalThis.fetch = mockFetch(409, {
    busy: true, reason: 'busy', state: 'idle',
    activity: 'Composer has unsubmitted text', composer_text: 'shaw was typing', payload,
  });
  const result = await sendToAgent('gm', { text: 'my message' });
  assert.strictEqual(isComposerHold(result), true);
  assert.strictEqual(isDelivered(result), false);
  assert.deepStrictEqual(result.payload, payload);
  assert.strictEqual(describeSendState(result), 'Composer has unsubmitted text — waiting');
  console.log('PASS: 409 composer-hold echoes payload for retry');
}

// --- attachments passed through -------------------------------------------
{
  globalThis.fetch = mockFetch(200, { state: 'delivered' });
  await sendToAgent('gm', { text: 'see attached', attachments: [{ upload_id: 'a.png' }] });
  const body = JSON.parse(mockFetch.lastInit.body);
  assert.deepStrictEqual(body.attachments, [{ upload_id: 'a.png' }]);
  console.log('PASS: attachments forwarded in the request body');
}

// --- force retry ------------------------------------------------------------
{
  globalThis.fetch = mockFetch(200, { state: 'delivered' });
  await sendToAgent('gm', { text: 'retry me' }, { force: true });
  const body = JSON.parse(mockFetch.lastInit.body);
  assert.strictEqual(body.force, true);
  console.log('PASS: force flag forwarded on retry');
}

// --- network failure never throws, surfaces as an error result ------------
{
  globalThis.fetch = async () => { throw new Error('ECONNREFUSED'); };
  const result = await sendToAgent('gm', { text: 'hi' });
  assert.strictEqual(result.httpStatus, 0);
  assert.strictEqual(result.error, 'ECONNREFUSED');
  assert.strictEqual(describeSendState(result), 'ECONNREFUSED');
  console.log('PASS: network failure surfaces as a result, never throws');
}

console.log('\nAll agentSend.ts tests passed.');
