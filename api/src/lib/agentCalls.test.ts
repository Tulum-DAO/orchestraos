/**
 * A call made on an agent's page is kept with that agent: listed and read per agent from the call journals (with the
 * agent's id in them), its thumbs saved, its "Voice chat" card placed in the agent's own chat where the call started,
 * and the spots where the agent's work started during the call (work anchors). Arturo's own calls (no agent) and
 * another agent's calls never show here. Real routes against a temp data dir and a temp Claude log.
 *
 * Run: npx tsx --test src/lib/agentCalls.test.ts   (from api/)
 */
import { testTmpDir } from './testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const SEAT = 'zz-calls-seat';                  // never a real tmux session on any box
const SID = '55555555-2222-4333-8444-000000000001';
const root = testTmpDir('agent-calls-');
const home = join(root, 'home');
const orch = join(root, 'orch');
const projects = join(home, '.claude', 'projects', '-repo');
mkdirSync(projects, { recursive: true });
mkdirSync(join(orch, 'state', 'agents'), { recursive: true });
mkdirSync(join(orch, 'state', 'voice-calls'), { recursive: true });
process.env.HOME = home;
process.env.ORCHESTRA_DIR = orch;
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
writeFileSync(join(orch, 'registry.json'), JSON.stringify({ agents: { [SEAT]: {} } }));
writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({ session_id: SID, cwd: '/repo' }));

const at = (s: string) => `2026-11-01T10:${s}Z`;
const epoch = (s: string) => Date.parse(at(s)) / 1000;
const user = (uuid: string, text: string, ts: string) => JSON.stringify({ type: 'user', uuid, timestamp: at(ts), message: { role: 'user', content: text } });
const reply = (uuid: string, text: string, ts: string) => JSON.stringify({ type: 'assistant', uuid, timestamp: at(ts),
  message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', content: [{ type: 'text', text }] } });
const tool = (uuid: string, ts: string) => JSON.stringify({ type: 'assistant', uuid, timestamp: at(ts),
  message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', content: [{ type: 'tool_use', id: `t-${uuid}`, name: 'Read', input: { file_path: '/repo/a' } }] } });
writeFileSync(join(projects, `${SID}.jsonl`), [
  user('a1', 'before the call', '00:00'), reply('a2', 'ok', '00:01'),
  // The call: 10:05 to 10:06. The first spoken turn runs two steps, the second none.
  user('c1', 'check the sample file', '05:05'), tool('c2', '05:06'), tool('c3', '05:07'), reply('c4', 'done', '05:08'),
  user('c5', 'thanks', '05:30'), reply('c6', 'any time', '05:31'),
  user('d1', 'after the call', '07:00'), tool('d2', '07:01'), reply('d3', 'ok', '07:02'),
].join('\n') + '\n');

const journal = (call_id: string, extra: Record<string, unknown>) =>
  writeFileSync(join(orch, 'state', 'voice-calls', `${call_id}.json`), JSON.stringify({ call_id, status: 'ended', origin: 'relay', ...extra }));
journal('vc_sample0001', { agent: SEAT, started_at: epoch('05:00'), ended_at: epoch('06:00'), turns: [
  { role: 'user', text: 'check the sample file', ts: epoch('05:05') },
  { role: 'agent', text: 'done', ts: epoch('05:08') },
  { role: 'tool', text: 'Read /repo/a', ts: epoch('05:06') },
  { role: 'user', text: 'thanks', ts: epoch('05:30') },
] });
journal('vc_arturo0001', { started_at: epoch('03:00'), ended_at: epoch('03:30'), turns: [] });             // Arturo's own
journal('vc_other00001', { agent: 'pm-demo', started_at: epoch('04:00'), ended_at: epoch('04:30'), turns: [] });
journal('vc_live000001', { agent: SEAT, status: 'live', started_at: epoch('08:00'), turns: [] });

const { default: callsRouter } = await import('../routes/agent-calls.js');
const { default: transcriptRouter } = await import('../routes/chat-transcript.js');
const { callWorkAnchors } = await import('./agentCalls.js');

async function withApp(fn: (base: string) => Promise<void>) {
  const app = express();
  app.use(express.json());
  app.use('/api/agents', callsRouter);
  app.use('/api/agents', transcriptRouter);
  const server = app.listen(0);
  try { await fn(`http://127.0.0.1:${(server.address() as AddressInfo).port}/api/agents`); } finally { server.close(); }
}
const json = async (r: Promise<globalThis.Response>) => { const x = await r; return { status: x.status, body: await x.json() as any }; };

test('the agent\'s calls, newest first; Arturo\'s and another agent\'s never', async () => {
  await withApp(async (base) => {
    const { body } = await json(fetch(`${base}/${SEAT}/calls`));
    assert.deepEqual(body.calls.map((c: any) => [c.call_id, c.status, c.duration_s]),
      [['vc_live000001', 'live', null], ['vc_sample0001', 'ended', 60]]);
    assert.equal(body.calls[1].started_at, at('05:00.000'));
  });
});

test('one call: its turns as You / the agent, and where its work started', async () => {
  await withApp(async (base) => {
    const { status, body } = await json(fetch(`${base}/${SEAT}/calls/vc_sample0001`));
    assert.equal(status, 200);
    assert.deepEqual(body.call.turns.map((t: any) => [t.role, t.text]),
      [['user', 'check the sample file'], ['agent', 'done'], ['user', 'thanks']], 'a tool run is not a turn');
    assert.deepEqual(body.call.work.map((w: any) => [w.uuid, w.steps]), [['c2', 2]], 'the first step of the turn that ran any');
    assert.equal((await json(fetch(`${base}/${SEAT}/calls/vc_arturo0001`))).status, 404, 'Arturo\'s call is not this agent\'s');
    assert.equal((await json(fetch(`${base}/${SEAT}/calls/vc_other00001`))).status, 404);
    assert.equal((await json(fetch(`${base}/${SEAT}/calls/bad id`))).status, 400);
  });
});

test('thumbs are saved per call, read back, and cleared; only on this agent\'s calls', async () => {
  await withApp(async (base) => {
    const put = (id: string, rating: unknown) => json(fetch(`${base}/${SEAT}/calls/${id}/rating`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rating }) }));
    assert.deepEqual((await put('vc_sample0001', 'up')).body, { ok: true, call_id: 'vc_sample0001', rating: 'up' });
    assert.equal((await json(fetch(`${base}/${SEAT}/calls/vc_sample0001`))).body.call.rating, 'up');
    const saved = JSON.parse(readFileSync(join(orch, 'state', 'voice-call-ratings.json'), 'utf-8'));
    assert.equal(saved.calls.vc_sample0001.rating, 'up');
    assert.ok(!('rating' in JSON.parse(readFileSync(join(orch, 'state', 'voice-calls', 'vc_sample0001.json'), 'utf-8'))),
      'the journal keeps its one writer');
    assert.equal((await put('vc_sample0001', 'sideways')).status, 400);
    assert.equal((await put('vc_other00001', 'down')).status, 404);
    await put('vc_sample0001', null);
    assert.equal((await json(fetch(`${base}/${SEAT}/calls/vc_sample0001`))).body.call.rating, null);
  });
});

test('the agent\'s chat shows the ended call as its "Voice chat" card where it started, and nothing else moves', async () => {
  await withApp(async (base) => {
    const { body } = await json(fetch(`${base}/${SEAT}/transcript`));
    const texts = body.items.map((i: any) => i.uuid ?? i.text);
    const card = texts.indexOf(`[voice-call: vc_sample0001 ${join(orch, 'state', 'voice-calls', 'vc_sample0001.json')}]`);
    assert.ok(card > texts.indexOf('a2') && card < texts.indexOf('c1'), 'between the turn before and the call\'s first turn');
    assert.ok(!texts.some((t: string) => /vc_live|vc_arturo|vc_other/.test(String(t))), 'a live call, Arturo\'s and another agent\'s: none');
    const item = body.items[card];
    assert.equal(item.uuid, undefined, 'no uuid: a client never pages from it');
    assert.deepEqual([item.role, item.call_id, item.duration_s, item.audience], ['user', 'vc_sample0001', 60, 'user']);
    const node = body.render_items.find((n: any) => n.key === 'call:vc_sample0001');
    assert.ok(node && node.kind === 'user' && /\[voice-call: vc_sample0001 /.test(node.text));
    const a2 = body.items.find((i: any) => i.uuid === 'a2');
    assert.equal(a2.audience, 'user', 'the reply before it keeps its place in the conversation');
  });
});

test('work anchors: one per turn the person opened during the call that ran a step; sub-steps do not count', () => {
  const items = [
    { kind: 'text', role: 'user', text: 'go', ts: at('05:05') },
    { kind: 'tool_use', uuid: 't1', ts: at('05:06') },
    { kind: 'tool_use', uuid: 't1b', ts: at('05:06'), parent_tool_use_id: 'x' },
    { kind: 'text', role: 'user', text: '[MSG from pm-demo]', machine: 'relay', ts: at('05:07') },
    { kind: 'tool_use', uuid: 't2', ts: at('05:08') },
    { kind: 'text', role: 'user', text: 'later', ts: at('09:00') },
    { kind: 'tool_use', uuid: 't3', ts: at('09:01') },
  ];
  assert.deepEqual(callWorkAnchors(items, at('05:00'), at('06:00')).map((a) => [a.uuid, a.steps]), [['t1', 2]],
    'a machine line does not open a turn: its steps belong to the person\'s');
  assert.deepEqual(callWorkAnchors(items, at('05:00'), null).map((a) => a.uuid), ['t1', 't3'], 'a live call is open-ended');
});
