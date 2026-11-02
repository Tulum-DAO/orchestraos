// A message typed in an agent's chat during a "voice"-mode call: the agent is told the call is live
// (one hidden tag), the call's relay is handed the words at once, and the chat never shows the tag.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, utimesSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import type { Request, Response } from 'express';
import { handleAgentSend, voiceCallLive, noteTypedOnCall, VOICE_CALL_LIVE_TAG, type AgentSendDeps } from './agent-send.js';
import { stripPageContext, normalizeTranscript } from './chat-transcript.js';

function req(body: any): Request {
  return { params: { id: 'pm-demo' }, body, headers: {} } as unknown as Request;
}
function fakeRes() {
  const calls: { status?: number; json?: any } = {};
  const res = { status(c: number) { calls.status = c; return res; }, json(b: any) { calls.json = b; return res; } } as unknown as Response;
  return { res, calls };
}
function deps(live: boolean, sent: string[], noted: string[]): AgentSendDeps {
  return {
    agentExists: () => true,
    resolveSession: (id) => id,
    gatewayInject: async (_s, text) => { sent.push(text); return { httpStatus: 200, ok: true, delivered: true, attempts: 1 }; },
    msgStoreSend: async () => ({ sent: true, id: 'msg_1' }),
    voiceCallLive: () => live,
    noteTypedOnCall: (_seat, text) => { noted.push(text); },
  };
}

test('on a live voice call: the agent gets the call tag and the relay gets the words', async () => {
  const sent: string[] = [], noted: string[] = [];
  await handleAgentSend(deps(true, sent, noted), req({ text: 'Test', page_context: { path: '/agent/pm-demo', title: 'pm' } }), fakeRes().res);
  assert.deepEqual(noted, ['Test']);
  assert.ok(sent[0].startsWith('Test <page-context>'));
  assert.ok(sent[0].endsWith(VOICE_CALL_LIVE_TAG));
  assert.equal(stripPageContext(sent[0]), 'Test');                     // the chat shows the words only
});

test('no call, or a slash command: unchanged', async () => {
  const sent: string[] = [], noted: string[] = [];
  await handleAgentSend(deps(false, sent, noted), req({ text: 'hi' }), fakeRes().res);
  await handleAgentSend(deps(true, sent, noted), req({ text: '/compact' }), fakeRes().res);
  assert.deepEqual(sent, ['hi', '/compact']);
  assert.deepEqual(noted, []);
});

test('voiceCallLive: a fresh marker and a switch that is on and not forced', () => {
  const d = mkdtempSync(join(tmpdir(), 'vcl-'));
  mkdirSync(join(d, 'state', 'headless', 'pm-demo'), { recursive: true });
  const sw = (row: any) => writeFileSync(join(d, 'state', 'agent-calls.json'), JSON.stringify({ seats: { 'pm-demo': row } }));
  sw({ on: true });
  assert.equal(voiceCallLive('pm-demo', d), false);                    // no marker: no call
  const mark = join(d, 'state', 'headless', 'pm-demo', 'call');
  writeFileSync(mark, '{}');
  assert.equal(voiceCallLive('pm-demo', d), true);
  assert.equal(voiceCallLive('pm-demo', d, Date.now() + 120_000), false);   // stale marker
  sw({ on: true, mode: 'forced' });
  assert.equal(voiceCallLive('pm-demo', d), false);
  sw({ on: false });
  assert.equal(voiceCallLive('pm-demo', d), false);
  assert.equal(voiceCallLive('../etc', d), false);
  utimesSync(mark, new Date(), new Date());
});

test('noteTypedOnCall appends one line beside the seat, only for a seat that exists', () => {
  const d = mkdtempSync(join(tmpdir(), 'vcl-'));
  noteTypedOnCall('pm-demo', 'nope', d);                               // no seat dir: nothing, no throw
  mkdirSync(join(d, 'state', 'headless', 'pm-demo'), { recursive: true });
  noteTypedOnCall('pm-demo', 'Test', d);
  noteTypedOnCall('pm-demo', 'Another test', d);
  const lines = readFileSync(join(d, 'state', 'headless', 'pm-demo', 'typed.jsonl'), 'utf-8').trim().split('\n').map((l) => JSON.parse(l));
  assert.deepEqual(lines.map((l) => l.text), ['Test', 'Another test']);
});

const userLine = (uuid: string, text: string) => JSON.stringify({ type: 'user', uuid, timestamp: '2026-11-02T10:00:00Z', message: { role: 'user', content: text } });
const agentLine = (uuid: string, text: string) => JSON.stringify({ type: 'assistant', uuid, timestamp: '2026-11-02T10:00:02Z', message: { role: 'assistant', content: [{ type: 'text', text }] } });
const SAID = '<voice-call-said>The operator SAID this to you on a live voice call.</voice-call-said>';

test('the chat hides a request said on the call and keeps the reply; a typed one shows without its tag', () => {
  const env = normalizeTranscript([
    userLine('u1', `send me a note ${SAID}`), agentLine('a1', 'Sent: your test came through.'),
    userLine('u2', `Test ${VOICE_CALL_LIVE_TAG}`),
  ], 'pm-demo', 'sample-session');
  // kept as the turn's opener (words only, flagged), never drawn
  const users = env.items.filter((i: any) => i.kind === 'text' && i.role === 'user').map((i: any) => [i.text, !!i.voice_said]);
  assert.deepEqual(users, [['send me a note', true], ['Test', false]]);
  assert.deepEqual(env.render_items.filter((n: any) => n.kind === 'user').map((n: any) => n.text), ['Test']);
  const reply = env.items.find((i: any) => i.role === 'assistant');
  assert.equal(reply.audience, 'user');                                 // still the voice turn's own answer, not folded
  assert.ok(env.items.some((i: any) => i.role === 'assistant' && /your test came through/.test(i.text)));
});
