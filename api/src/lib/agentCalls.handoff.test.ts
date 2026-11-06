/**
 * The call hand-off: POST /api/agents/:id/calls/handoff gives the agent its own call as one message (the transcript
 * inside <voice-call-transcript>, with the instruction to carry on in text), once per call; the chat hides that tag
 * and draws the call's card on that line, where the call ended, and not also where it started. Real routes against a
 * temp data dir and a temp Claude log; the delivery is a stand-in for the gateway.
 *
 * Run: npx tsx --test src/lib/agentCalls.handoff.test.ts   (from api/)
 */
import { testTmpDir } from './testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { createHash } from 'node:crypto';
import { mkdirSync, writeFileSync, appendFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';

const SEAT = 'zz-handoff-seat';                // never a real tmux session on any box
const SID = '66666666-2222-4333-8444-000000000001';
const root = testTmpDir('call-handoff-');
const home = join(root, 'home');
const orch = join(root, 'orch');
const projects = join(home, '.claude', 'projects', '-repo');
const calls = join(orch, 'state', 'voice-calls');
mkdirSync(projects, { recursive: true });
mkdirSync(join(orch, 'state', 'agents'), { recursive: true });
mkdirSync(calls, { recursive: true });
process.env.HOME = home;
process.env.ORCHESTRA_DIR = orch;
if (!process.env.ORCHESTRA_CONFIG) process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
writeFileSync(join(orch, 'registry.json'), JSON.stringify({ agents: { [SEAT]: {}, 'pm-demo': {} } }));
writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({ session_id: SID, cwd: '/repo' }));

const NOW = Date.parse('2026-11-01T12:00:00Z');
const ago = (min: number) => (NOW - min * 60_000) / 1000;
const iso = (min: number) => new Date(NOW - min * 60_000).toISOString();
const clientId = (conv: string) => `vc_client_${createHash('sha1').update(conv).digest('hex').slice(0, 12)}`;
const journal = (call_id: string, extra: Record<string, unknown>) =>
  writeFileSync(join(calls, `${call_id}.json`), JSON.stringify({ call_id, status: 'ended', origin: 'relay', ...extra }));
const turns = [
  { role: 'user', text: 'Can you check the sample build?', ts: ago(4) },
  { role: 'arturo', text: 'Sure, I will look at it after the call.', ts: ago(3.9) },
  { role: 'tool', text: 'Read /repo/a', ts: ago(3.8) },
];
// The call the browser names (ended 3 min ago), a newer one (1 min ago), an old one, and Arturo's own.
journal('vc_named000001', { agent: SEAT, conv_id: 'conv-sample-a', started_at: ago(4), ended_at: ago(3), turns });
journal('vc_newest00001', { agent: SEAT, conv_id: 'conv-sample-b', started_at: ago(2), ended_at: ago(1), turns: [{ role: 'user', text: 'hello', ts: ago(2) }] });
journal('vc_old00000001', { agent: SEAT, conv_id: 'conv-sample-c', started_at: ago(40), ended_at: ago(30), turns: [] });
journal('vc_arturo00001', { conv_id: 'conv-sample-d', started_at: ago(2), ended_at: ago(1), turns: [] });

const user = (uuid: string, text: string, min: number) => JSON.stringify({ type: 'user', uuid, timestamp: iso(min), message: { role: 'user', content: text } });
const reply = (uuid: string, text: string, min: number) => JSON.stringify({ type: 'assistant', uuid, timestamp: iso(min),
  message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', content: [{ type: 'text', text }] } });
const LOG = join(projects, `${SID}.jsonl`);
writeFileSync(LOG, [user('a1', 'earlier words', 50), reply('a2', 'ok', 49.9), user('a3', 'just before the calls', 5), reply('a4', 'ok', 4.9)].join('\n') + '\n');

const { createAgentCallsRouter } = await import('../routes/agent-calls.js');
const { default: transcriptRouter } = await import('../routes/chat-transcript.js');
const { HANDOFF_INSTRUCTION, callMarkerText } = await import('./agentCalls.js');

const sent: { session: string; text: string }[] = [];
let gwAnswer: any = { httpStatus: 200, ok: true, delivered: true };
let storeOk = true;
const router = createAgentCallsRouter({
  dataDir: () => orch, chatItems: () => [], now: () => NOW,
  resolveSession: (id) => id,
  gatewayInject: async (session, text) => { sent.push({ session, text }); return gwAnswer; },
  msgStoreSend: async () => (storeOk ? { sent: true, id: 'msg_sample' } : { sent: false, error: 'down' }),
  agentName: () => 'Sample Agent',
});

async function withApp(fn: (base: string) => Promise<void>) {
  const app = express();
  app.use(express.json());
  app.use('/api/agents', router);
  app.use('/api/agents', transcriptRouter);
  const server = app.listen(0);
  try { await fn(`http://127.0.0.1:${(server.address() as AddressInfo).port}/api/agents`); } finally { server.close(); }
}
const handoff = (base: string, id: string, body: unknown = {}, headers: Record<string, string> = {}) =>
  fetch(`${base}/${id}/calls/handoff`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) })
    .then(async (r) => ({ status: r.status, body: await r.json() as any }));
const reset = () => { rmSync(join(orch, 'state', 'voice-call-handoffs.json'), { force: true }); sent.length = 0; gwAnswer = { httpStatus: 200, ok: true, delivered: true }; storeOk = true; };

test('the browser\'s id finds its call through the record\'s conversation id; one message, built on the server', async () => {
  reset();
  await withApp(async (base) => {
    const r = await handoff(base, SEAT, { client_call_id: clientId('conv-sample-a') });
    assert.deepEqual(r, { status: 200, body: { ok: true, call_id: 'vc_named000001', matched: 'client_id', state: 'delivered' } });
    assert.equal(sent.length, 1);
    assert.equal(sent[0].session, SEAT);
    assert.equal(sent[0].text, `<voice-call-transcript id="vc_named000001" duration="01:00">\n${HANDOFF_INSTRUCTION}\n\n`
      + 'You: Can you check the sample build?\nSample Agent: Sure, I will look at it after the call.\n</voice-call-transcript>');
  });
});

test('without the browser\'s id (or with one that names no recent call): the newest call that just ended', async () => {
  reset();
  await withApp(async (base) => {
    assert.equal((await handoff(base, SEAT)).body.call_id, 'vc_newest00001');
    reset();
    const r = await handoff(base, SEAT, { client_call_id: clientId('conv-sample-c') });
    assert.deepEqual([r.body.call_id, r.body.matched], ['vc_newest00001', 'latest'], 'a call older than ten minutes is not matched');
    assert.equal((await handoff(base, SEAT, { client_call_id: '../x' })).status, 400);
  });
});

test('once per call: a second hand-off is a no-op 200, and nothing is sent again', async () => {
  reset();
  await withApp(async (base) => {
    await handoff(base, SEAT, { client_call_id: clientId('conv-sample-a') });
    const again = await handoff(base, SEAT, { client_call_id: clientId('conv-sample-a') });
    assert.deepEqual(again.body, { ok: true, call_id: 'vc_named000001', matched: 'client_id', already: true, state: 'delivered' });
    assert.equal(sent.length, 1);
  });
});

test('not delivered anywhere: forgotten, so a retry sends it; a busy seat gets it as mail', async () => {
  reset();
  await withApp(async (base) => {
    gwAnswer = { httpStatus: 409, ok: false, busy: true, state: 'waiting' };
    storeOk = false;
    assert.equal((await handoff(base, SEAT)).status, 502);
    storeOk = true;
    const r = await handoff(base, SEAT);
    assert.deepEqual([r.status, r.body.state], [200, 'queued']);
    assert.equal(sent.length, 2, 'the retry was really sent');
  });
});

test('never for Arturo, never another agent\'s call, and nothing recent is a 404', async () => {
  reset();
  await withApp(async (base) => {
    assert.deepEqual((await handoff(base, 'arturo')).body, { ok: false, error: 'not_for_arturo' });
    assert.deepEqual(await handoff(base, 'pm-demo'), { status: 404, body: { ok: false, error: 'no_recent_call' } });
    assert.equal(sent.length, 0);
  });
});

test('an agent the caller may not see is a 404, before anything is read or sent', async () => {
  reset();
  const prev = process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
  process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = '1';
  try {
    await withApp(async (base) => {
      const r = await handoff(base, SEAT, {}, { 'x-orchestra-user': 'sample-user', 'x-orchestra-role': 'viewer', 'x-orchestra-allowed-agents': 'pm-demo' });
      assert.equal(r.status, 404);
      assert.equal(sent.length, 0);
    });
  } finally {
    if (prev === undefined) delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS; else process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = prev;
  }
});

test('the chat hides the hand-off and draws exactly one card for that call, on the hand-off line', async () => {
  reset();
  await withApp(async (base) => {
    // Before any hand-off: each call's card where it started.
    const before = await (await fetch(`${base}/${SEAT}/transcript`)).json() as any;
    const cards = (b: any, id: string) => b.items.filter((i: any) => typeof i.text === 'string' && i.text.includes(`[voice-call: ${id} `));
    assert.equal(cards(before, 'vc_named000001').length, 1);
    // The hand-off, and the line it leaves in the agent's log, then the agent's reply.
    await handoff(base, SEAT, { client_call_id: clientId('conv-sample-a') });
    appendFileSync(LOG, [user('h1', sent[0].text, 0.5), reply('h2', 'I will check the sample build now.', 0.4)].join('\n') + '\n');
    const after = await (await fetch(`${base}/${SEAT}/transcript`)).json() as any;
    const mine = cards(after, 'vc_named000001');
    assert.equal(mine.length, 1, 'one card, not one at the start and one at the hand-off');
    assert.equal(mine[0].uuid, 'h1', 'on the hand-off line itself');
    assert.equal(mine[0].text, callMarkerText(orch, 'vc_named000001'));
    assert.ok(!after.items.some((i: any) => typeof i.text === 'string' && /voice-call-transcript|own voice call/.test(i.text)), 'the tag and its words are hidden');
    assert.equal(after.render_items.filter((n: any) => n.kind === 'user' && n.text.includes('[voice-call: vc_named000001 ')).length, 1);
    const ids = after.items.map((i: any) => i.uuid);
    assert.ok(ids.indexOf('h1') > ids.indexOf('a4') && ids.indexOf('h1') < ids.indexOf('h2'), 'where the call ended, before the reply');
    assert.equal(cards(after, 'vc_newest00001').length, 1, 'a call not handed off keeps its card where it started');
  });
});
