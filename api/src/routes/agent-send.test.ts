/**
 * B1 — POST /api/agents/:id/send (Agent Page v1, DEC-1789508247033721).
 * Drives handleAgentSend() directly with fake req/res + injected deps (the
 * gatewayInject/msgStoreSend boundary), same pattern as
 * unified-approvals.menu.test.ts — no supertest, no real HTTP server.
 *
 * Run: npx tsx --test src/routes/agent-send.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Request, Response } from 'express';
import {
  handleAgentSend,
  buildMessageText,
  clientWantsSendStates,
  type AgentSendDeps,
  type GatewayInjectResult,
  type MsgStoreSendResult,
  MSG_STORE,
} from './agent-send.js';
import { resolveMsgStorePath } from './messages.js';

function req(opts: { params?: any; body?: any; headers?: Record<string, string> } = {}): Request {
  return {
    params: opts.params || { id: 'gm' },
    body: opts.body || {},
    headers: opts.headers || {},
  } as unknown as Request;
}

function fakeRes() {
  const calls: { status?: number; json?: any } = {};
  const res = {
    status(code: number) { calls.status = code; return res; },
    json(body: any) { calls.json = body; return res; },
  } as unknown as Response;
  return { res, calls };
}

function deps(overrides: Partial<AgentSendDeps> = {}): AgentSendDeps {
  return {
    agentExists: (id: string) => true,
    resolveSession: (id: string) => `session-${id}`,
    gatewayInject: async (): Promise<GatewayInjectResult> => ({ httpStatus: 200, ok: true, delivered: true, attempts: 1 }),
    msgStoreSend: async (): Promise<MsgStoreSendResult> => ({ sent: true, id: 'msg_test1' }),
    ...overrides,
  };
}

// ── delivered ────────────────────────────────────────────────────────────

test('delivered: capable client gets {state:"delivered"}', async () => {
  const { res, calls } = fakeRes();
  await handleAgentSend(deps(), req({
    body: { text: 'hi', client_caps: ['send-states'] },
  }), res);
  assert.equal(calls.status, 200);
  assert.equal(calls.json.state, 'delivered');
  assert.equal(calls.json.session, 'session-gm');
});

test('delivered: legacy client (no caps) gets {ok:true}, no state field', async () => {
  const { res, calls } = fakeRes();
  await handleAgentSend(deps(), req({ body: { text: 'hi' } }), res);
  assert.equal(calls.status, 200);
  assert.equal(calls.json.ok, true);
  assert.equal(calls.json.state, undefined);
});

// ── queued (durable-first fallback, non-prompt busy / unreachable) ───────

test('queued: gateway unreachable -> durable write -> {state:"queued"}', async () => {
  const { res, calls } = fakeRes();
  let sawWrite: any = null;
  const d = deps({
    gatewayInject: async () => ({ httpStatus: 0, ok: false, unreachable: true, error: 'ECONNREFUSED' }),
    msgStoreSend: async (params) => { sawWrite = params; return { sent: true, id: 'msg_q1' }; },
  });
  await handleAgentSend(d, req({ body: { text: 'do the thing', client_caps: ['send-states'] } }), res);
  assert.equal(calls.status, 200);
  assert.equal(calls.json.state, 'queued');
  assert.equal(calls.json.message_id, 'msg_q1');
  assert.ok(sawWrite);
  assert.equal(sawWrite.toAgent, 'session-gm');
  assert.match(sawWrite.body, /do the thing/);
});

test('queued: pane stopped (non-prompt busy) -> durable write -> queued', async () => {
  const { res, calls } = fakeRes();
  const d = deps({
    gatewayInject: async () => ({ httpStatus: 502, ok: false, reason: 'send_failed', state: 'stopped' }),
  });
  await handleAgentSend(d, req({ body: { text: 'x', client_caps: ['send-states'] } }), res);
  assert.equal(calls.json.state, 'queued');
});

// ── held (menu/permission prompt showing, and gateway-side mid-turn hold) ─

test('held: pane shows a menu/permission prompt (state=waiting) -> durable write -> held', async () => {
  const { res, calls } = fakeRes();
  const d = deps({
    gatewayInject: async () => ({ httpStatus: 409, ok: false, reason: 'busy', state: 'waiting', activity: 'Awaiting approval' }),
  });
  await handleAgentSend(d, req({ body: { text: 'x', client_caps: ['send-states'] } }), res);
  assert.equal(calls.status, 200);
  assert.equal(calls.json.state, 'held');
  assert.match(calls.json.reason, /menu\/permission prompt/);
});

test('held: gateway auto-converts a mid-turn working busy via accepts:held', async () => {
  const { res, calls } = fakeRes();
  const d = deps({
    gatewayInject: async () => ({ httpStatus: 200, ok: true, held: true, message_id: 'msg_held1' }),
  });
  await handleAgentSend(d, req({ body: { text: 'x', client_caps: ['send-states'] } }), res);
  assert.equal(calls.status, 200);
  assert.equal(calls.json.state, 'held');
  assert.equal(calls.json.message_id, 'msg_held1');
});

test('held: durable write itself fails -> never lie, 502 with state held', async () => {
  const { res, calls } = fakeRes();
  const d = deps({
    gatewayInject: async () => ({ httpStatus: 409, ok: false, reason: 'busy', state: 'waiting' }),
    msgStoreSend: async () => ({ sent: false, error: 'sqlite locked' }),
  });
  await handleAgentSend(d, req({ body: { text: 'x', client_caps: ['send-states'] } }), res);
  assert.equal(calls.status, 502);
  assert.equal(calls.json.state, 'held');
  assert.equal(calls.json.reason, 'durable_write_failed');
});

// The 502 error string used to be whatever msgStoreSend returned. On the real
// failure path that is an execFileSync message, which carries the entire argv:
// the msg_store.py path, --from/--to, and the mkdtemp body-file path. That is
// server-side detail and belongs in the log, not in a browser response.
// MUTATION: put `error: err` back as the raw write.error and this goes red.
test('502: the client body carries no command line, local path or tmp dir', async () => {
  const leaky =
    "Command failed: python3 /Users/someone/orchestraos/msg_store.py send --from gm --to build " +
    "--body-file /var/folders/xy/T/agent-send-Ab3d/body.txt\nsqlite3.OperationalError: database is locked";

  for (const caps of [['send-states'], []]) {
    const { res, calls } = fakeRes();
    const d = deps({
      gatewayInject: async () => ({ httpStatus: 409, ok: false, reason: 'busy', state: 'waiting' }),
      msgStoreSend: async () => ({ sent: false, error: leaky }),
    });
    await handleAgentSend(d, req({ body: { text: 'x', client_caps: caps } }), res);

    assert.equal(calls.status, 502, 'still a 502 — the status is not what leaked');
    const wire = JSON.stringify(calls.json);
    for (const secret of ['msg_store.py', '/Users/', '/var/folders/', 'body.txt', 'python3', '--body-file']) {
      assert.ok(!wire.includes(secret), `502 body leaked ${secret}: ${wire}`);
    }
    // ...and it must still SAY something, or the UI renders an empty red box.
    assert.ok(String(calls.json.error || '').length > 0, 'the client still needs a reason to show');
  }
});

// ── D4: composer-hold -> 409 with payload echoed, never queued underneath ─

test('409: composer has the operator\'s own unsubmitted text -> payload echoed, no durable write', async () => {
  const { res, calls } = fakeRes();
  let wrote = false;
  const d = deps({
    gatewayInject: async () => ({
      httpStatus: 409, ok: false, reason: 'busy', state: 'idle',
      activity: 'Composer has unsubmitted text', composer_text: 'operator was mid-sentence',
    }),
    msgStoreSend: async () => { wrote = true; return { sent: true, id: 'should-not-happen' }; },
  });
  await handleAgentSend(d, req({ body: { text: 'my new message', client_caps: ['send-states'] } }), res);
  assert.equal(calls.status, 409);
  assert.equal(calls.json.busy, true);
  assert.equal(calls.json.composer_text, 'operator was mid-sentence');
  assert.deepEqual(calls.json.payload, { text: 'my new message', attachments: undefined, client_caps: ['send-states'] });
  assert.equal(wrote, false, 'must never durable-write on top of a composer-hold');
});

test('409: stranded_input state -> payload echoed', async () => {
  const { res, calls } = fakeRes();
  const d = deps({
    gatewayInject: async () => ({
      httpStatus: 409, ok: false, reason: 'busy', state: 'stranded',
      stranded: { text: 'orphaned', age_s: 42 },
    }),
  });
  await handleAgentSend(d, req({ body: { text: 'x' } }), res);
  assert.equal(calls.status, 409);
  assert.deepEqual(calls.json.stranded, { text: 'orphaned', age_s: 42 });
  assert.ok(calls.json.payload);
});

// ── capability gate (D5) ──────────────────────────────────────────────────

test('clientWantsSendStates: body client_caps array', () => {
  assert.equal(clientWantsSendStates(req({ body: { client_caps: ['send-states'] } })), true);
});

test('clientWantsSendStates: X-Client-Capabilities header, comma-separated', () => {
  assert.equal(clientWantsSendStates(req({ headers: { 'x-client-capabilities': 'foo, send-states, bar' } })), true);
});

test('clientWantsSendStates: neither present -> false (legacy client)', () => {
  assert.equal(clientWantsSendStates(req({ body: { text: 'hi' } })), false);
});

// ── 400 validation ─────────────────────────────────────────────────────────

test('400: no text and no attachments', async () => {
  const { res, calls } = fakeRes();
  await handleAgentSend(deps(), req({ body: {} }), res);
  assert.equal(calls.status, 400);
});

// ── 404 unknown agent ────────────────────────────────────────────────────────

test('404: unknown agent id -> 404, no msg_store send, no tmux call', async () => {
  const { res, calls } = fakeRes();
  let resolveSessionCalled = false;
  let msgStoreSendCalled = false;
  const d = deps({
    agentExists: (id: string) => false,
    resolveSession: (id: string) => { resolveSessionCalled = true; return `session-${id}`; },
    msgStoreSend: async () => { msgStoreSendCalled = true; return { sent: true, id: 'should-not-send' }; },
  });
  await handleAgentSend(d, req({ params: { id: '__no_such_agent__' }, body: { text: 'probe' } }), res);
  assert.equal(calls.status, 404);
  assert.equal(calls.json.ok, false);
  assert.equal(calls.json.error, 'unknown agent');
  assert.equal(calls.json.agent, '__no_such_agent__');
  assert.equal(resolveSessionCalled, false, 'must not call resolveSession for unknown agent');
  assert.equal(msgStoreSendCalled, false, 'must not call msgStoreSend for unknown agent');
});

// ── marker grammar (extends ChatInput.tsx's existing [IMAGE:]/[FILE:] tags) ─

test('buildMessageText: no attachments -> plain trimmed text', () => {
  assert.equal(buildMessageText('  hello  ', undefined), 'hello');
});

test('buildMessageText: image attachment gets an [IMAGE: path] marker prefix', () => {
  const out = buildMessageText('caption', [{ upload_id: '123-abc.png' }]);
  assert.match(out, /^\[IMAGE: .*123-abc\.png\] caption$/);
});

test('buildMessageText: non-image attachment gets [FILE: path]', () => {
  const out = buildMessageText('', [{ upload_id: '123-abc.pdf' }]);
  assert.match(out, /^\[FILE: .*123-abc\.pdf\]$/);
});

test('MSG_STORE resolves to the CODE checkout, never the data dir', () => {
  // test found (2026-09-30) that this was join(ORCHESTRA_DIR, 'msg_store.py') — the DATA dir,
  // a path that has never existed. So the durable-first fallback shelled out to a missing file
  // and answered {state:'held', reason:'durable_write_failed'} at HTTP 502: the safety net for
  // a pane that is down or busy could not write anywhere, the message was genuinely gone, and
  // the response read as safely-queued. Reproducible 100% of the time.
  //
  // The regression this guards is not "the path is wrong" but "someone recomputes the path
  // here instead of reusing the helper that already got it right". ORCHESTRA_DIR is the data
  // dir (state/, queue/, logs/); msg_store.py is CODE and lives in the checkout.
  const root = resolveMsgStorePath({ ORCHESTRA_ROOT: '/opt/orchestraos' }, import.meta.url);
  assert.equal(root, '/opt/orchestraos/msg_store.py');

  // With no ORCHESTRA_ROOT it walks up from the module (<root>/api/dist/routes/x.js), so it
  // still lands in the checkout rather than anywhere under the data dir.
  const derived = resolveMsgStorePath({}, import.meta.url);
  assert.ok(derived.endsWith('/msg_store.py'), derived);
  assert.ok(!derived.includes('/api/'), `must not resolve inside api/: ${derived}`);

  // The specific wrong answer must never come back: a data dir is not a code checkout.
  const wrong = resolveMsgStorePath({ ORCHESTRA_DIR: '/home/u/.orchestra' } as Record<string, string>, import.meta.url);
  assert.ok(!wrong.startsWith('/home/u/.orchestra'),
    'ORCHESTRA_DIR must not be consulted for a code path');

  // AND — the part that actually guards the bug. The assertions above only prove the HELPER
  // works; they pass just as happily while this module ignores it. I wrote them first, then
  // mutated MSG_STORE back to join(ORCHESTRA_DIR, 'msg_store.py') and NOTHING went red: a
  // vacuous guard for the very defect it was written for. This asserts the value the module
  // actually uses.
  assert.equal(MSG_STORE, resolveMsgStorePath(process.env, import.meta.url),
    'agent-send must USE the shared resolver, not recompute the path');
  assert.ok(!MSG_STORE.includes('/.orchestra/'),
    `MSG_STORE must not point into the data dir: ${MSG_STORE}`);
  assert.ok(MSG_STORE.endsWith('/msg_store.py'), MSG_STORE);
});
