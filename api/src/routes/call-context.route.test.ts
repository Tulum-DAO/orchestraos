/**
 * GET /api/agents/:id/call-context: the opening context of a call with this agent, from the real routes against a temp
 * data dir and a temp Claude log. Loopback only and never through a proxy; the starting call left out; a previous call
 * still marked live included; a planted secret never served; the budget clamped.
 *
 * Run: npx tsx --test src/routes/call-context.route.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const SEAT = 'zz-callctx-seat';
const SID = '55555555-2222-4333-8444-0000000000c1';
const root = testTmpDir('call-context-route-');
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
writeFileSync(join(orch, 'state', 'shaw-location.json'), JSON.stringify({ tz: 'America/Cancun', label: 'Tulum' }));
mkdirSync(join(orch, 'state', 'arturo'), { recursive: true });
writeFileSync(join(orch, 'state', 'arturo', 'operator.json'), JSON.stringify({ facts: { name: { value: 'Shaw' } } }));

const at = (s: string) => `2026-11-25T17:${s}Z`;
const epoch = (s: string) => Date.parse(at(s)) / 1000;
const user = (uuid: string, text: string, ts: string) => JSON.stringify({ type: 'user', uuid, timestamp: at(ts), message: { role: 'user', content: text } });
const reply = (uuid: string, text: string, ts: string) => JSON.stringify({ type: 'assistant', uuid, timestamp: at(ts),
  message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', content: [{ type: 'text', text }] } });
writeFileSync(join(projects, `${SID}.jsonl`), [
  user('a1', 'is the dream fix in? my token is sk-proj_abcdefghijklmnop', '00:00'), reply('a2', 'Yes, merged at 06:09Z.', '00:30'),
].join('\n') + '\n');
const journal = (call_id: string, extra: Record<string, unknown>) =>
  writeFileSync(join(orch, 'state', 'voice-calls', `${call_id}.json`), JSON.stringify({ call_id, ...extra }));
journal('vc_prev00001', { agent: SEAT, status: 'live', started_at: epoch('10:00'),
  turns: [{ role: 'user', text: 'remember the roadmap call', ts: epoch('10:00') }, { role: 'agent', text: 'noted', ts: epoch('10:05') }] });
journal('vc_now000001', { agent: SEAT, status: 'live', started_at: epoch('20:00'),
  turns: [{ role: 'user', text: 'the call now starting', ts: epoch('20:00') }] });
journal('vc_arturo001', { started_at: epoch('05:00'), turns: [{ role: 'user', text: 'an Arturo call', ts: epoch('05:00') }] });

const { default: transcriptRouter } = await import('./chat-transcript.js');

async function withApp(fn: (base: string) => Promise<void>) {
  const app = express();
  app.use('/api/agents', transcriptRouter);
  const server = app.listen(0, '127.0.0.1');
  await new Promise((r) => server.once('listening', r));
  try { await fn(`http://127.0.0.1:${(server.address() as AddressInfo).port}/api/agents`); } finally { server.close(); }
}

test('the chat and the agent\'s earlier calls, stamped, oldest first; the starting call and Arturo\'s left out', async () => {
  await withApp(async (base) => {
    const r = await fetch(`${base}/${SEAT}/call-context?exclude=vc_now000001`);
    assert.equal(r.status, 200);
    assert.match(r.headers.get('content-type') || '', /^text\/plain/);
    assert.equal(r.headers.get('cache-control'), 'no-store');
    const t = await r.text();
    assert.ok(t.includes('[2026-11-25T17:00:00Z 12:00 Tulum] chat Shaw: is the dream fix in?'), t);
    assert.ok(t.includes(`call vc_prev00001 ${SEAT}: noted`));
    assert.ok(t.indexOf('Yes, merged') < t.indexOf('remember the roadmap call'));
    assert.ok(!t.includes('the call now starting') && !t.includes('an Arturo call'));
    assert.ok(!t.includes('abcdefghijklmnop'));
  });
});

test('calls=arturo merges Arturo\'s own calls with this chat', async () => {
  await withApp(async (base) => {
    const t = await (await fetch(`${base}/${SEAT}/call-context?calls=arturo`)).text();
    assert.ok(t.includes('call vc_arturo001 Shaw: an Arturo call') && !t.includes('remember the roadmap call'));
  });
});

test('through a proxy it does not exist; bad parameters are refused', async () => {
  await withApp(async (base) => {
    assert.equal((await fetch(`${base}/${SEAT}/call-context`, { headers: { 'X-Forwarded-For': '203.0.113.9' } })).status, 404);
    assert.equal((await fetch(`${base}/${SEAT}/call-context`, { headers: { 'Tailscale-Funnel-Request': '?1' } })).status, 404);
    assert.equal((await fetch(`${base}/${SEAT}/call-context?exclude=../../x`)).status, 400);
    assert.equal((await fetch(`${base}/${SEAT}/call-context?calls=../x`)).status, 400);
  });
});

test('a tiny budget is clamped up, not honoured to nothing', async () => {
  await withApp(async (base) => {
    const t = await (await fetch(`${base}/${SEAT}/call-context?budget=1`)).text();
    assert.ok(t.includes('RECENT (verbatim):') && t.includes('noted'));
  });
});
