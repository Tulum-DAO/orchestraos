/**
 * POST /api/agents/:id/send-now {message_id}: a person's message Claude Code already holds in its own queue (the send
 * answered outcome "delivered-steer"), submitted at once with the CLI's own send-now (scripts/watch_gateway.py
 * _send_now; measured on Claude Code 2.1.284, docs/DELIVERY.md "Send now"). And the message_id that send now carries.
 * Drives the handlers directly with fake req/res + injected deps, the agent-send.test.ts pattern.
 *
 * Run: npx tsx --test src/routes/agent-send.send-now.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Request, Response } from 'express';
import {
  handleAgentSend,
  handleAgentSendNow,
  type AgentSendDeps,
  type AgentSendNowDeps,
  type GatewayInjectResult,
  type GatewaySendNowResult,
} from './agent-send.js';

const MID = 'stq_0123456789abcdef';

function req(opts: { params?: any; body?: any; headers?: Record<string, string> } = {}): Request {
  return { params: opts.params || { id: 'gm' }, body: opts.body || {}, headers: opts.headers || {}, query: {} } as unknown as Request;
}

function fakeRes() {
  const calls: { status?: number; json?: any } = {};
  const res = {
    status(code: number) { calls.status = code; return res; },
    json(body: any) { calls.json = body; return res; },
  } as unknown as Response;
  return { res, calls };
}

function nowDeps(answer: GatewaySendNowResult, seen: any[] = []): AgentSendNowDeps {
  return {
    agentExists: () => true,
    resolveSession: (id) => `session-${id}`,
    gatewaySendNow: async (session, messageId) => { seen.push({ session, messageId }); return answer; },
  };
}

async function sendNow(answer: GatewaySendNowResult, body: any = { message_id: MID }, seen: any[] = []) {
  const { res, calls } = fakeRes();
  await handleAgentSendNow(nowDeps(answer, seen), req({ body }), res);
  return calls;
}

// ── the message_id on a delivered-steer send ─────────────────────────────

function sendDeps(gw: GatewayInjectResult): AgentSendDeps {
  return {
    agentExists: () => true,
    resolveSession: (id) => `session-${id}`,
    gatewayInject: async () => gw,
    msgStoreSend: async () => ({ sent: true, id: 'msg_x' }),
  };
}

test('send: a delivered-steer answer carries message_id, and the old answer is a subset of the new one', async () => {
  const base: GatewayInjectResult = { httpStatus: 200, ok: true, delivered: true, attempts: 1, state: 'working', outcome: 'delivered-steer' };
  const before = fakeRes();
  await handleAgentSend(sendDeps(base), req({ body: { text: 'hi', client_caps: ['send-states'] } }), before.res);
  const after = fakeRes();
  await handleAgentSend(sendDeps({ ...base, message_id: MID }), req({ body: { text: 'hi', client_caps: ['send-states'] } }), after.res);
  assert.equal(after.calls.status, 200);
  assert.equal(after.calls.json.message_id, MID);
  const { message_id: _drop, ...rest } = after.calls.json;
  assert.deepEqual(rest, before.calls.json, 'additive only: every old field keeps its value');
});

test('send: message_id is passed only on delivered-steer and only in the minted shape', async () => {
  for (const gw of [
    { httpStatus: 200, ok: true, delivered: true, outcome: 'delivered-now' as const, message_id: MID },
    { httpStatus: 200, ok: true, delivered: true, outcome: 'interrupted' as const, message_id: MID },
    { httpStatus: 200, ok: true, delivered: true, outcome: 'delivered-steer' as const, message_id: 'msg_from_a_supervisor' },
  ]) {
    const { res, calls } = fakeRes();
    await handleAgentSend(sendDeps(gw), req({ body: { text: 'hi', client_caps: ['send-states'] } }), res);
    assert.equal(calls.json.message_id, undefined, JSON.stringify(gw));
  }
});

// ── send-now ─────────────────────────────────────────────────────────────

test('send-now: delivered-now passes the outcome, step_ended and also_submitted, and asks for this seat and id', async () => {
  const seen: any[] = [];
  const calls = await sendNow({ httpStatus: 200, body: { ok: true, outcome: 'delivered-now', message_id: MID,
    step_ended: 'truncated', also_submitted: ['task-notification'] } }, { message_id: MID }, seen);
  assert.equal(calls.status, 200);
  assert.deepEqual(calls.json, { outcome: 'delivered-now', session: 'session-gm', message_id: MID,
    step_ended: 'truncated', also_submitted: ['task-notification'] });
  assert.deepEqual(seen, [{ session: 'session-gm', messageId: MID }]);
});

test('send-now: a step_ended word nobody knows reads unknown, never none', async () => {
  const calls = await sendNow({ httpStatus: 200, body: { outcome: 'delivered-now', step_ended: 'paused' } });
  assert.equal(calls.json.step_ended, 'unknown');
  assert.deepEqual(calls.json.also_submitted, []);
});

test('send-now: already-sent and not-queued are 200 with no step fields', async () => {
  for (const outcome of ['already-sent', 'not-queued']) {
    const calls = await sendNow({ httpStatus: 200, body: { ok: true, outcome } });
    assert.equal(calls.status, 200);
    assert.deepEqual(calls.json, { outcome, session: 'session-gm', message_id: MID });
  }
});

test('send-now: a refusal is 409 with the gateway reason and its full sentence', async () => {
  const calls = await sendNow({ httpStatus: 409, body: { ok: false, outcome: 'refused', reason: 'other-queued', queued: 1,
    error: 'Another message is queued for this agent too, and send-now would submit it as well; nothing was sent.' } });
  assert.equal(calls.status, 409);
  assert.equal(calls.json.outcome, 'refused');
  assert.equal(calls.json.reason, 'other-queued');
  assert.equal(calls.json.queued, 1);
  assert.match(calls.json.error, /nothing was sent\.$/);
});

test('send-now: someone at the pane (a draft, an attached terminal) is 423, never 409, with its full sentence', async () => {
  let calls = await sendNow({ httpStatus: 423, body: { outcome: 'refused', reason: 'draft-in-composer',
    error: "Text is typed in this agent's composer, and send-now would submit it too; send or clear it first. Nothing was sent." } });
  assert.equal(calls.status, 423);
  assert.equal(calls.json.outcome, 'refused');
  assert.equal(calls.json.reason, 'draft-in-composer');
  assert.match(calls.json.error, /send or clear it first/);
  calls = await sendNow({ httpStatus: 423, body: { outcome: 'refused', reason: 'terminal-attached',
    error: "Someone has this agent's terminal open; send it from there." } });
  assert.equal(calls.status, 423);
  assert.equal(calls.json.error, "Someone has this agent's terminal open; send it from there.");
});

test('send-now: unknown (502) and a timeout are both state unknown', async () => {
  let calls = await sendNow({ httpStatus: 502, body: { state: 'unknown', outcome: 'unknown', error: 'It may still go.' } });
  assert.equal(calls.status, 502);
  assert.equal(calls.json.state, 'unknown');
  calls = await sendNow({ httpStatus: 0, body: {}, maybeSent: true });
  assert.equal(calls.status, 502);
  assert.equal(calls.json.state, 'unknown');
});

test('send-now: an unreachable gateway is 502 and says nothing was sent (not unknown)', async () => {
  const calls = await sendNow({ httpStatus: 0, body: {}, unreachable: true });
  assert.equal(calls.status, 502);
  assert.equal(calls.json.state, undefined);
  assert.match(calls.json.error, /nothing was sent/);
});

test('send-now: another runtime is 501 unsupported', async () => {
  const calls = await sendNow({ httpStatus: 501, body: { outcome: 'unsupported', error: 'Send-now works only for Claude Code agents; this one has no such key.' } });
  assert.equal(calls.status, 501);
  assert.equal(calls.json.outcome, 'unsupported');
});

test('send-now: a malformed id is 400 and the gateway is never asked', async () => {
  for (const body of [{}, { message_id: 'msg_123' }, { message_id: MID + '0' }, { message_id: 7 }]) {
    const seen: any[] = [];
    const calls = await sendNow({ httpStatus: 200, body: { outcome: 'delivered-now' } }, body, seen);
    assert.equal(calls.status, 400, JSON.stringify(body));
    assert.equal(seen.length, 0);
  }
});

test('send-now: an unknown agent is 404 and the gateway is never asked', async () => {
  const seen: any[] = [];
  const { res, calls } = fakeRes();
  await handleAgentSendNow({ ...nowDeps({ httpStatus: 200, body: {} }, seen), agentExists: () => false }, req({ body: { message_id: MID } }), res);
  assert.equal(calls.status, 404);
  assert.equal(seen.length, 0);
});

test('send-now: an unexpected gateway answer never forwards its raw body', async () => {
  const calls = await sendNow({ httpStatus: 400, body: { ok: false, error: 'session and a message_id ... /home/someone/x' } });
  assert.equal(calls.status, 502);
  assert.doesNotMatch(JSON.stringify(calls.json), /home/);
});

// ── review r1 (fenced Claude, BLOCK @ 64815b69) ──────────────────────────

test('send-now: a gateway 500 (it may have sent the keys) is unknown, never "nothing was sent"', async () => {
  const calls = await sendNow({ httpStatus: 500, body: {} });
  assert.equal(calls.status, 502);
  assert.equal(calls.json.state, 'unknown');
  assert.doesNotMatch(calls.json.error, /nothing was sent/i);
});

test('send-now: an unknown id is its own 404 with the gateway sentence', async () => {
  const calls = await sendNow({ httpStatus: 404, body: { ok: false, outcome: 'unknown-id',
    error: 'That message is not known here (older than a day, or never queued); nothing was sent.' } });
  assert.equal(calls.status, 404);
  assert.equal(calls.json.outcome, 'unknown-id');
  assert.match(calls.json.error, /not known here/);
});

test('send-now: only a refused connection says nothing was sent; a reset after the request went is unknown', async () => {
  const { postAgentSendNow } = await import('./agent-send.js');
  const realFetch = globalThis.fetch;
  const fail = (code: string) => { globalThis.fetch = (async () => { throw Object.assign(new TypeError('fetch failed'), { cause: { code } }); }) as any; };
  try {
    fail('ECONNREFUSED');
    let r = await postAgentSendNow('s', 'stq_0123456789abcdef', { tokenFile: () => 'tok' });
    assert.equal(r.unreachable, true);
    fail('ECONNRESET');
    r = await postAgentSendNow('s', 'stq_0123456789abcdef', { tokenFile: () => 'tok' });
    assert.equal(r.unreachable, undefined);
    assert.equal(r.maybeSent, true);
  } finally {
    globalThis.fetch = realFetch;
  }
});


// ── review r2 (MERGE-OK @ 0bfe3fda, non-blocking folds) ──────────────────

test('send-now: a 2xx the API cannot read (no known outcome) is unknown, never "nothing was sent"', async () => {
  for (const body of [{}, { outcome: 'sent-ish' }]) {
    const calls = await sendNow({ httpStatus: 200, body });
    assert.equal(calls.status, 502, JSON.stringify(body));
    assert.equal(calls.json.state, 'unknown');
  }
});
