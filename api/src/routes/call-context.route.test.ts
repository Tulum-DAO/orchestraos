/**
 * GET /api/agents/:id/call-context: the opening context of a call with this agent, from the real routes against a temp
 * data dir and a temp Claude log. Loopback only and never through a proxy; the starting call left out; a previous call
 * still marked live included; this install's gateway token never served, even cut at the clip boundary; the budget
 * clamped; and the shared fixture's route case served byte for byte as the Python twin renders it.
 *
 * Run: npx tsx --test src/routes/call-context.route.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import Database from 'better-sqlite3';
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
const FIX = JSON.parse(readFileSync(new URL('../../../contract/call-context-cases.json', import.meta.url), 'utf-8'))
  .cases.find((c: any) => c.route);
const FSID = '55555555-2222-4333-8444-0000000000c2';
const fproj = join(home, '.claude', 'projects', '-fixture');
mkdirSync(fproj, { recursive: true });
writeFileSync(join(orch, 'registry.json'), JSON.stringify({ agents: { [SEAT]: {}, [FIX.chat_agent]: {} } }));
writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({ session_id: SID, cwd: '/repo' }));
writeFileSync(join(orch, 'state', 'agents', `${FIX.chat_agent}.json`), JSON.stringify({ session_id: FSID, cwd: '/fixture' }));
// This install's gateway token, under the temp data dir only (never the live config's).
const TOKEN = 'gwtok_' + 'Q7'.repeat(20);
writeFileSync(join(orch, 'state', 'watch-gateway-token'), TOKEN + '\n');
process.env.WATCH_GATEWAY_TOKEN_FILE = join(orch, 'state', 'watch-gateway-token');
// A token that contains "sk-": the exact match must run before the shapes, or the shape eats only its tail.
const SK_TOKEN = 'R8'.repeat(20) + 'sk-abcdef';
// A phone message held for delivery (msg_store held_message): the operator's own words, shown in the chat.
const MSGDB = join(root, 'tasks.db');
{
  const db = new Database(MSGDB);
  db.exec(`CREATE TABLE messages (
    id TEXT PRIMARY KEY, type TEXT NOT NULL, from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
    body TEXT, status TEXT NOT NULL DEFAULT 'pending', metadata TEXT,
    created_at TEXT NOT NULL, delivered_at TEXT, acknowledged_at TEXT);`);
  db.prepare(`INSERT INTO messages (id,type,from_agent,to_agent,body,status,created_at)
    VALUES ('h1','held_message','operator',?,'held on the phone: did the fix land?','pending','2026-11-25T17:05:00.000000+00:00')`).run(SEAT);
  db.close();
}
process.env.MSG_DB_PATH = MSGDB;
writeFileSync(join(orch, 'state', 'shaw-location.json'), JSON.stringify({ tz: 'America/Cancun', label: 'Tulum' }));
mkdirSync(join(orch, 'state', 'arturo'), { recursive: true });
writeFileSync(join(orch, 'state', 'arturo', 'operator.json'), JSON.stringify({ facts: { name: { value: 'Shaw' } } }));

const at = (s: string) => `2026-11-25T17:${s}Z`;
const epoch = (s: string) => Date.parse(at(s)) / 1000;
const user = (uuid: string, text: string, ts: string) => JSON.stringify({ type: 'user', uuid, timestamp: at(ts), message: { role: 'user', content: text } });
const reply = (uuid: string, text: string, ts: string) => JSON.stringify({ type: 'assistant', uuid, timestamp: at(ts),
  message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', content: [{ type: 'text', text }] } });
// The token straddles the 1200-character clip: redacted before the clip, or a near-complete prefix would ship.
const straddle = 'x'.repeat(1170) + ' ' + TOKEN + ' tail';
writeFileSync(join(projects, `${SID}.jsonl`), [
  user('a1', 'is the dream fix in? my token is sk-proj_abcdefghijklmnop', '00:00'), reply('a2', 'Yes, merged at 06:09Z.', '00:30'),
  user('a3', straddle, '00:40'), user('a4', `again ${TOKEN}`, '00:50'), user('a5', `sk token ${SK_TOKEN} end`, '00:55'),
  user('a6', 'tokens: 12345, password: reset, see https://x.test/a?key=abcd ok', '00:56'),
].join('\n') + '\n');
// The fixture's route case: its chat as a Claude log, its journals beside the others.
const iso = (ts: string) => ts.replace(/(\.\d{3})?Z$/, '.000Z');
writeFileSync(join(fproj, `${FSID}.jsonl`), FIX.items.map((it: any, i: number) => JSON.stringify(it.role === 'user'
  ? { type: 'user', uuid: `f${i}`, timestamp: iso(it.ts), message: { role: 'user', content: it.text } }
  : { type: 'assistant', uuid: `f${i}`, timestamp: iso(it.ts), message: { id: `m-f${i}`, role: 'assistant', model: 'claude-opus-5-5', content: [{ type: 'text', text: it.text }] } })).join('\n') + '\n');
for (const j of FIX.journals) writeFileSync(join(orch, 'state', 'voice-calls', j.name), JSON.stringify(j.doc));
const journal = (call_id: string, extra: Record<string, unknown>) =>
  writeFileSync(join(orch, 'state', 'voice-calls', `${call_id}.json`), JSON.stringify({ call_id, ...extra }));
journal('vc_prev00001', { agent: SEAT, status: 'live', started_at: epoch('10:00'),
  turns: [{ role: 'user', text: 'remember the roadmap call', ts: epoch('10:00') }, { role: 'agent', text: 'noted', ts: epoch('10:05') }] });
journal('vc_now000001', { agent: SEAT, status: 'live', started_at: epoch('20:00'),
  turns: [{ role: 'user', text: 'the call now starting', ts: epoch('20:00') }] });
journal('vc_arturo001', { started_at: epoch('05:00'), turns: [{ role: 'user', text: 'an Arturo call', ts: epoch('05:00') }] });

const { default: transcriptRouter, directLoopback, callOwnerVisible } = await import('./chat-transcript.js');
const { buildCallContext } = await import('../lib/callContext.js');

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
    assert.ok(!t.includes(TOKEN.slice(0, 20)), 'the gateway token (or a prefix of it) was served');
    assert.ok(t.includes('again [redacted'));
  });
});

test('a held phone message is the operator\'s chat too', async () => {
  await withApp(async (base) => {
    const t = await (await fetch(`${base}/${SEAT}/call-context`)).text();
    assert.match(t, /^\[2026-11-25T17:05:00Z 12:05 Tulum\] chat Shaw: held on the phone: did the fix land\?$/m);
  });
});

test('a gateway token that contains "sk-" is blanked whole, not just its tail', async () => {
  const tokFile = process.env.WATCH_GATEWAY_TOKEN_FILE as string;
  writeFileSync(tokFile, SK_TOKEN + '\n');
  try {
    await withApp(async (base) => {
      const t = await (await fetch(`${base}/${SEAT}/call-context`)).text();
      assert.ok(!t.includes('R8'.repeat(10)), 'the part of the token before "sk-" was served');
      assert.ok(t.includes('sk token [redacted'));
    });
  } finally {
    writeFileSync(tokFile, TOKEN + '\n');
  }
});

test('a token file shorter than 8 characters blanks nothing (the 8-char floor python and redactSecrets apply)', async () => {
  const tokFile = process.env.WATCH_GATEWAY_TOKEN_FILE as string;
  writeFileSync(tokFile, 'dream\n');
  try {
    await withApp(async (base) => {
      const t = await (await fetch(`${base}/${SEAT}/call-context`)).text();
      assert.ok(t.includes('is the dream fix in?'), 'a short token file blanked ordinary words');
    });
  } finally {
    writeFileSync(tokFile, TOKEN + '\n');
  }
});

test('the route\'s redactor is a superset of the twin\'s: these secret-free shapes differ, by design', async () => {
  const line = 'tokens: 12345, password: reset, see https://x.test/a?key=abcd ok';
  const twin = buildCallContext({ chatAgent: 'gm', callOwner: null, journals: [], tz: 'UTC', tzLabel: 'UTC',
    items: [{ kind: 'text', role: 'user', text: line, ts: '2026-11-25T17:00:56Z', audience: 'user' }] });
  assert.ok(twin.includes('tokens: 12345, password: reset'));       // the twin leaves these two (no "=")
  await withApp(async (base) => {
    const t = await (await fetch(`${base}/${SEAT}/call-context`)).text();
    assert.ok(t.includes('tokens: [redacted: named secret]') && t.includes('password: [redacted: named secret]'));
    assert.ok(t.includes('?key=[redacted: URL token]') && !t.includes('tokens: 12345, password: reset'));
  });
});

test('calls=<owner> is scoped like :id; Arturo\'s own calls need a principal that sees every agent', () => {
  const admin = { allowedAgents: '*' } as any;
  const some = { allowedAgents: ['dev-a'] } as any;
  const client = { allowedAgents: '*', clientScope: 'acme' } as any;
  const tags = (id: string) => (id === 'dev-c' ? ['client:acme'] : []);
  assert.equal(callOwnerVisible(admin, 'dev-b', tags), true);
  assert.equal(callOwnerVisible(some, 'dev-a', tags), true);
  assert.equal(callOwnerVisible(some, 'dev-b', tags), false);
  assert.equal(callOwnerVisible(client, 'dev-b', tags), false);
  assert.equal(callOwnerVisible(client, 'dev-c', tags), true);
  assert.equal(callOwnerVisible(admin, null, tags), true);
  assert.equal(callOwnerVisible(some, null, tags), false);
  assert.equal(callOwnerVisible(client, null, tags), false);
  assert.equal(callOwnerVisible(null, null, tags), false);
});

test('the fixture route case: the same bytes the Python twin renders', async () => {
  await withApp(async (base) => {
    const t = await (await fetch(`${base}/${FIX.chat_agent}/call-context?budget=${FIX.budget}`)).text();
    assert.equal(t, FIX.expected);
  });
});

test('calls=arturo merges Arturo\'s own calls with this chat', async () => {
  await withApp(async (base) => {
    const t = await (await fetch(`${base}/${SEAT}/call-context?calls=arturo`)).text();
    assert.ok(t.includes('call vc_arturo001 Shaw: an Arturo call') && !t.includes('remember the roadmap call'));
  });
});

const PROXY_HEADERS: Record<string, string> = {
  'X-Forwarded-For': '203.0.113.9', 'X-Forwarded-Host': 'box.ts.net', 'X-Forwarded-Proto': 'https',
  Forwarded: 'for=203.0.113.9', 'X-Real-IP': '203.0.113.9', 'Tailscale-Funnel-Request': '?1',
  'Tailscale-User-Login': 'someone@example.com', 'X-Orchestra-Peer': 'mac',
};

test('only a direct caller on this box: every proxy header and a non-loopback address refused', () => {
  const req = (addr: string, headers: Record<string, string> = {}) => ({ socket: { remoteAddress: addr }, headers });
  assert.equal(directLoopback(req('127.0.0.1')), true);
  assert.equal(directLoopback(req('::ffff:127.0.0.1')), true);
  assert.equal(directLoopback(req('100.64.0.7')), false);
  assert.equal(directLoopback(req('203.0.113.9')), false);
  for (const [h, v] of Object.entries(PROXY_HEADERS)) assert.equal(directLoopback(req('127.0.0.1', { [h.toLowerCase()]: v })), false, h);
});

test('through a proxy it does not exist; bad parameters are refused', async () => {
  await withApp(async (base) => {
    for (const [h, v] of Object.entries(PROXY_HEADERS)) {
      assert.equal((await fetch(`${base}/${SEAT}/call-context`, { headers: { [h]: v } })).status, 404, h);
    }
    assert.equal((await fetch(`${base}/${SEAT}/call-context?exclude=../../x`)).status, 400);
    assert.equal((await fetch(`${base}/${SEAT}/call-context?calls=../x`)).status, 400);
  });
});

test('a tiny budget is clamped up, not honoured to nothing', async () => {
  await withApp(async (base) => {
    const t = await (await fetch(`${base}/${SEAT}/call-context?budget=1`)).text();
    // honoured, a budget of 1 keeps nothing verbatim; clamped to 500 the newest call line is there in full
    assert.match(t, new RegExp(`^\\[[^\\]]+\\] call vc_prev00001 ${SEAT}: noted$`, 'm'));
  });
});

test('?meta=1 says what the call will know, before the call: whose chat, whose calls, how many, how big', async () => {
  await withApp(async (base) => {
    const r = await fetch(`${base}/${SEAT}/call-context?meta=1&exclude=vc_now000001&budget=1500`);
    assert.equal(r.status, 200);
    assert.match(r.headers.get('content-type') || '', /^application\/json/);
    assert.equal(r.headers.get('cache-control'), 'no-store');
    const m = await r.json();
    assert.deepEqual(Object.keys(m).sort(), ['calls', 'chat', 'opening_tokens', 'previous_calls']);
    assert.equal(m.chat, SEAT);
    assert.equal(m.calls, SEAT);
    assert.equal(m.previous_calls, 1);              // vc_prev00001; the starting call and Arturo's are not counted
    const text = await (await fetch(`${base}/${SEAT}/call-context?exclude=vc_now000001&budget=1500`)).text();
    assert.equal(m.opening_tokens, Math.ceil(Array.from(text).length / 4));
    const arturo = await (await fetch(`${base}/${SEAT}/call-context?meta=1&calls=arturo`)).json();
    assert.equal(arturo.calls, 'arturo');
  });
});

test('meta carries no text, so a proxied (dashboard) request gets it; the text itself stays loopback-only', async () => {
  await withApp(async (base) => {
    const hdr = { headers: { 'x-forwarded-for': '100.64.0.9' } };
    assert.equal((await fetch(`${base}/${SEAT}/call-context`, hdr)).status, 404);
    const r = await fetch(`${base}/${SEAT}/call-context?meta=1`, hdr);
    assert.equal(r.status, 200);
    const m = await r.json();
    assert.equal(m.chat, SEAT);
    assert.ok(!JSON.stringify(m).includes('dream fix'), 'meta leaked thread text');
  });
});
