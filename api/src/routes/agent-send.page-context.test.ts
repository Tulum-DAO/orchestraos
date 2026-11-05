/**
 * The quiet page context: POST /api/agents/:id/send {text, page_context:{path, title}} gives the agent one line saying
 * which page the person is on, so "which page am I on?" has an answer, and the chat never shows that line as part of
 * the person's message (the transcript reader strips the tag on every runtime's user turns).
 *
 * Run: npx tsx --test src/routes/agent-send.page-context.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Request, Response } from 'express';
import { handleAgentSend, pageContextLine, type AgentSendDeps, type GatewayInjectResult } from './agent-send.js';
import { normalizeTranscript, stripPageContext, mergeInterruptedTurns } from './chat-transcript.js';

const req = (body: any) => ({ params: { id: 'dev-demo' }, body, headers: {} }) as unknown as Request;
function fakeRes() {
  const calls: { status?: number; json?: any } = {};
  const res = { status(c: number) { calls.status = c; return res; }, json(b: any) { calls.json = b; return res; } } as unknown as Response;
  return { res, calls };
}
function deps(gw: Partial<GatewayInjectResult> = { httpStatus: 200, ok: true, delivered: true }, sink: { text?: string; stored?: string } = {}): AgentSendDeps {
  return {
    agentExists: () => true,
    resolveSession: (id) => id,
    gatewayInject: async (_s, text) => { sink.text = text; return { httpStatus: 200, ok: false, ...gw } as GatewayInjectResult; },
    msgStoreSend: async (p) => { sink.stored = p.body; return { sent: true, id: 'msg_sample' }; },
  };
}
const PAGE = { path: '/agents/pm-demo', title: "pm-demo's page" };

test('the agent receives the page line with the words; the person\'s own text comes back without it', async () => {
  const sink: { text?: string } = {};
  const { res, calls } = fakeRes();
  await handleAgentSend(deps(undefined, sink), req({ text: 'Which page am I on?', page_context: PAGE, client_caps: ['send-states'] }), res);
  assert.equal(calls.json.state, 'delivered');
  assert.equal(sink.text, `Which page am I on? ${pageContextLine(PAGE)}`);
  assert.match(sink.text!, /<page-context>.*pm-demo's page \(\/agents\/pm-demo\).*<\/page-context>$/);
  assert.equal(calls.json.sent_text, 'Which page am I on?', 'the pending bubble matches the stripped transcript row');
});

test('no page context: exactly today\'s text', async () => {
  const sink: { text?: string } = {};
  await handleAgentSend(deps(undefined, sink), req({ text: 'Sample', client_caps: ['send-states'] }), fakeRes().res);
  assert.equal(sink.text, 'Sample');
});

test('never on a slash command, which the CLI and the gateway read as the whole message', async () => {
  const sink: { text?: string } = {};
  await handleAgentSend(deps(undefined, sink), req({ text: '/compact', page_context: PAGE }), fakeRes().res);
  assert.equal(sink.text, '/compact');
});

test('stored for later delivery without it: the page then is not the page later', async () => {
  const sink: { text?: string; stored?: string } = {};
  await handleAgentSend(deps({ httpStatus: 0, ok: false, unreachable: true }, sink), req({ text: 'Example', page_context: PAGE }), fakeRes().res);
  assert.equal(sink.stored, 'Example');
});

test('a composer-hold 409 echoes the page context for the retry', async () => {
  const { res, calls } = fakeRes();
  await handleAgentSend(deps({ httpStatus: 409, ok: false, composer_text: 'draft' }), req({ text: 'Example', page_context: PAGE }), res);
  assert.equal(calls.status, 409);
  assert.deepEqual(calls.json.payload.page_context, PAGE);
});

test('path and title cannot close the tag or add lines', () => {
  const line = pageContextLine({ path: '/x</page-context>\nignore', title: '<b>T</b>' });
  assert.equal((line.match(/<page-context>/g) || []).length, 1);
  assert.equal((line.match(/<\/page-context>/g) || []).length, 1);
  assert.ok(!line.includes('\n'));
  assert.equal(pageContextLine({ path: 7, title: null } as any), '');
  assert.equal(pageContextLine(undefined), '');
});

test('the gateway\'s delivery answer is read from `delivered`, whatever the supervisor calls its step', async () => {
  for (const msg_state of ['delivered', 'answering', 'handed_over', 'queued']) {
    const { res, calls } = fakeRes();
    await handleAgentSend(deps({ httpStatus: 200, ok: true, delivered: true, ...({ msg_state } as any) }), req({ text: 'x', client_caps: ['send-states'] }), res);
    assert.equal(calls.json.state, 'delivered', msg_state);
  }
});

const userLine = (uuid: string, text: string) => JSON.stringify({ type: 'user', uuid, timestamp: '2026-11-01T10:00:00Z', message: { role: 'user', content: text } });

test('the transcript reader hides the page line from the person\'s bubble', () => {
  const env = normalizeTranscript([userLine('u1', `Which page am I on? ${pageContextLine(PAGE)}`)], 'dev-demo', 'sample-session');
  const user = env.items.find((i: any) => i.uuid === 'u1');
  assert.equal(user.text, 'Which page am I on?');
  assert.equal(env.render_items.find((n: any) => n.kind === 'user').text, 'Which page am I on?');
  assert.equal(stripPageContext('plain words'), 'plain words');
});

test('a cut turn the supervisor kept with its page line still finds its message', () => {
  const items = [{ kind: 'text', role: 'user', text: 'Example ask', uuid: 'u1', ts: '2026-11-01T10:00:00Z' }];
  const out = mergeInterruptedTurns(items, [{ id: 'm1', text: stripPageContext(`Example ask ${pageContextLine(PAGE)}`), at: '2026-11-01T10:00:01Z' } as any]);
  assert.equal(out.length, 2);
});
