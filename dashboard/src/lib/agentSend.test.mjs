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
  isFailed,
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
    activity: 'Composer has unsubmitted text', composer_text: 'operator was typing', payload,
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

// --- 502 durable_write_failed must NOT read as accepted (FIX 1, CRITICAL) ---
// agent-send.ts answers HTTP 502 with state:'held' when msg_store's durable
// write fails. The state enum says "held" but nothing was stored. Before the
// httpStatus gate, isHeld() read that as accepted and all four UI call sites
// showed success and cleared the operator's typed text.
//
// MUTATION CHECK: delete the `accepted(result) &&` guard from isHeld() in
// agentSend.ts and this block goes red on the isHeld assertion.
{
  globalThis.fetch = mockFetch(502, {
    state: 'held',
    reason: 'durable_write_failed',
    error: 'Could not store the message — it was not delivered.',
  });
  const result = await sendToAgent('gm', { text: 'do not lose me' });

  assert.strictEqual(result.httpStatus, 502);
  assert.strictEqual(result.state, 'held', 'server really does send state:held here — that is the trap');
  assert.strictEqual(isHeld(result), false, 'a 502 is not a hold, however the body is labelled');
  assert.strictEqual(isDelivered(result), false);
  assert.strictEqual(isQueued(result), false);
  assert.strictEqual(isFailed(result), true);
  assert.strictEqual(isComposerHold(result), false, 'not the 409 retryable path');

  // The message the operator actually sees must say it did not arrive, and must
  // never be empty — an empty string renders as a silent red with no reason.
  const shown = describeSendState(result);
  assert.strictEqual(shown, 'Could not store the message — it was not delivered.');
  assert.notStrictEqual(shown, '');

  // The behaviour each call site derives from those helpers. These are the exact
  // expressions in the components — if any goes true, that component clears the
  // operator's text on a message that was never stored.
  //   AgentCard.tsx:315          accepted = isDelivered || isQueued || isHeld
  //   ChatInput.tsx:266          isDelivered || isQueued || isHeld
  //   AgentDetailPanel.tsx:227   !isDelivered && !isQueued && !isHeld  -> error branch
  //   Composer.tsx:54-57         {ok, queued, held} -> ChatInput's onSend seam
  assert.strictEqual(isDelivered(result) || isQueued(result) || isHeld(result), false,
    'AgentCard/ChatInput would clear the input on this');
  assert.strictEqual(!isDelivered(result) && !isQueued(result) && !isHeld(result), true,
    'AgentDetailPanel must take its error branch, which returns before setText("")');
  const composerSeam = {
    ok: isDelivered(result),
    queued: isQueued(result),
    held: isHeld(result) || isComposerHold(result),
  };
  assert.deepStrictEqual(composerSeam, { ok: false, queued: false, held: false },
    'ChatInput onSend seam must fall through to Failed, not the busy/held affordance');

  console.log('PASS: 502 durable_write_failed is a failure at every call site, not a hold');
}

// --- a real 200 hold is still a hold (the fix must not over-correct) -------
// MUTATION CHECK: make accepted() return false unconditionally and this goes red.
{
  globalThis.fetch = mockFetch(200, { state: 'held', reason: 'busy_working' });
  const result = await sendToAgent('gm', { text: 'hi' });
  assert.strictEqual(isHeld(result), true);
  assert.strictEqual(isFailed(result), false);
  assert.strictEqual(describeSendState(result), 'busy_working');
  console.log('PASS: a 200 hold is still accepted — the gate is on status, not on state');
}

// --- other non-2xx answers are failures too --------------------------------
{
  for (const [status, body] of [
    [500, { ok: false, error: 'internal error' }],
    [404, { ok: false, error: 'unknown agent', agent: 'nope' }],
    [401, { ok: false, error: 'unauthenticated' }],
  ]) {
    globalThis.fetch = mockFetch(status, body);
    const result = await sendToAgent('nope', { text: 'hi' });
    assert.strictEqual(isFailed(result), true, `${status} must be a failure`);
    assert.strictEqual(isDelivered(result) || isQueued(result) || isHeld(result), false);
    assert.notStrictEqual(describeSendState(result), '', `${status} must explain itself`);
  }
  console.log('PASS: 500/404/401 are failures with a non-empty explanation');
}

// --- a failure with no error field still explains itself -------------------
// Guards the empty-red-box case: a bare non-2xx with no body.
{
  globalThis.fetch = mockFetch(503, {});
  const result = await sendToAgent('gm', { text: 'hi' });
  assert.strictEqual(isFailed(result), true);
  assert.strictEqual(describeSendState(result), 'Send failed (HTTP 503) — not delivered');
  console.log('PASS: a bodiless non-2xx still produces a readable error');
}

// --- 409 composer-hold is NOT a plain failure ------------------------------
// It is retryable with force and has its own affordance; isFailed() must not
// swallow it into the generic Failed branch.
{
  globalThis.fetch = mockFetch(409, {
    busy: true, activity: 'Composer has unsubmitted text',
    payload: { text: 'x', client_caps: ['send-states'] },
  });
  const result = await sendToAgent('gm', { text: 'x' });
  assert.strictEqual(isComposerHold(result), true);
  assert.strictEqual(isFailed(result), false, '409 keeps its force-retry path');
  console.log('PASS: 409 composer-hold is not folded into isFailed');
}

console.log('\nAll agentSend.ts tests passed.');
