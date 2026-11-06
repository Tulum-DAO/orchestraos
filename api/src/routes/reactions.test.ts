/**
 * /api/reactions REST (spec "API"): GET a conversation's reactions grouped by ref, PUT to set (same emoji again
 * removes), DELETE to remove; the reactor is lib/reactor.ts (an agent header without the token counts as the
 * operator). Real express app on an ephemeral port, isolated store.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/routes/reactions.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';

const dir = testTmpDir('reactions-route-');
process.env.ORCHESTRA_DIR = dir;
process.env.ORCHESTRA_AGENT_REACT_TOKEN_FILE = join(dir, 'agent-react-token');
writeFileSync(process.env.ORCHESTRA_AGENT_REACT_TOKEN_FILE, 'tok-route-123\n', { mode: 0o600 });
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;

const express = (await import('express')).default;
const { createReactionsRouter, isEmoji, parseTarget } = await import('./reactions.js');
const { ReactionStore } = await import('../lib/reactions-store.js');
const { loadConfig } = await import('../lib/config.js');
const OP = loadConfig().operatorId;

const store = new ReactionStore(join(dir, 'state', 'reactions.db'));
const app = express();
app.use(express.json());
app.use('/api/reactions', createReactionsRouter({ store: () => store }));
const server = app.listen(0);
after(() => { server.closeAllConnections?.(); server.close(); store.close(); rmSync(dir, { recursive: true, force: true }); });
const base = `http://127.0.0.1:${(server.address() as any).port}/api/reactions`;

const REF = '11111111-2222-4333-8444-000000000001~u1#0';
const AGENT = { 'x-orchestra-agent': 'pm-x', 'x-orchestra-agent-token': 'tok-route-123' };

async function call(method: string, opts: { body?: unknown; headers?: Record<string, string>; q?: string } = {}) {
  const r = await fetch(`${base}${opts.q ?? ''}`, {
    method,
    headers: { 'content-type': 'application/json', ...(opts.headers ?? {}) },
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });
  return { status: r.status, json: (await r.json()) as any };
}
const put = (conv: string, emoji: string, headers?: Record<string, string>, ref = REF) =>
  call('PUT', { body: { surface: 'agent-chat', conv, ref, emoji }, headers });
const list = async (conv: string, surface = 'agent-chat') =>
  (await call('GET', { q: `?surface=${surface}&conv=${encodeURIComponent(conv)}` })).json;

test('isEmoji: single emoji (with modifiers, flags, keycaps, ZWJ) yes; words and blanks no', () => {
  for (const e of ['👍', '❤️', '🇺🇸', '1️⃣', '👍🏽', '👨‍👩‍👧', '🎉']) assert.equal(isEmoji(e), true, e);
  for (const e of ['', 'hello', ' 👍', 'a👍', '👍 ', '🎉'.repeat(20), 42, null]) assert.equal(isEmoji(e), false, String(e));
});

test('isEmoji: exactly one grapheme, no ASCII (keycaps aside)', () => {
  for (const e of ['👍👍👍', '1👍', '<👍>', '👍❤️']) assert.equal(isEmoji(e), false, e);
  for (const e of ['👍🏽', '❤️', '👨‍👩‍👧', '1️⃣']) assert.equal(isEmoji(e), true, e);
});

test('parseTarget: an agent-chat ref has exactly one ~', () => {
  assert.ok('error' in parseTarget({ surface: 'agent-chat', conv: 'pm-a', ref: 's~u~v#0' }, true));
});

test('parseTarget: surface, conv and the ref shape per surface', () => {
  assert.deepEqual(parseTarget({ surface: 'agent-chat', conv: 'pm-a', ref: REF }, true), { surface: 'agent-chat', conv: 'pm-a', ref: REF });
  assert.deepEqual(parseTarget({ surface: 'arturo', conv: 'thr_1', ref: '12' }, true), { surface: 'arturo', conv: 'thr_1', ref: '12' });
  assert.ok('error' in parseTarget({ surface: 'mail', conv: 'pm-a', ref: REF }, true), 'unknown surface');
  assert.ok('error' in parseTarget({ surface: 'agent-chat', conv: '../x', ref: REF }, true), 'unsafe agent id');
  assert.ok('error' in parseTarget({ surface: 'agent-chat', conv: 'pm-a', ref: 'u1' }, true), 'agent-chat ref needs session~uuid#part');
  assert.ok('error' in parseTarget({ surface: 'agent-chat', conv: 'pm-a', ref: 'n3' }, true), 'a positional key is not a ref');
  assert.ok('error' in parseTarget({ surface: 'arturo', conv: 'thr_1', ref: REF }, true), 'arturo ref is a seq');
  assert.ok('error' in parseTarget({ surface: 'agent-chat', conv: 'pm-a' }, true), 'ref required');
  assert.deepEqual(parseTarget({ surface: 'agent-chat', conv: 'pm-a' }, false), { surface: 'agent-chat', conv: 'pm-a', ref: '' });
});

test('PUT: one emoji per reactor per message, a new pick replaces it', async () => {
  const a = await put('pm-one', '👍');
  assert.equal(a.status, 200);
  assert.equal(a.json.ok, true);
  assert.deepEqual([a.json.surface, a.json.conv, a.json.ref, a.json.reactor, a.json.emoji],
    ['agent-chat', 'pm-one', REF, { kind: 'user', id: OP }, '👍']);
  assert.match(a.json.at, /^\d{4}-\d{2}-\d{2}T/);
  const b = await put('pm-one', '❤️');
  assert.equal(b.json.emoji, '❤️');
  const got = await list('pm-one');
  assert.deepEqual([got.surface, got.conv], ['agent-chat', 'pm-one']);
  assert.deepEqual(got.reactions[REF].map((r: any) => r.emoji), ['❤️']);
});

test('PUT the same emoji again removes it', async () => {
  await put('pm-toggle', '👍');
  const off = await put('pm-toggle', '👍');
  assert.equal(off.status, 200);
  assert.equal(off.json.emoji, null);
  assert.deepEqual((await list('pm-toggle')).reactions, {});
});

test('an agent with the token is its own reactor, next to the operator', async () => {
  await put('pm-agent', '👍');
  const a = await put('pm-agent', '🎉', AGENT);
  assert.deepEqual(a.json.reactor, { kind: 'agent', id: 'pm-x' });
  const got = await list('pm-agent');
  assert.deepEqual(got.reactions[REF].map((r: any) => [r.reactor.kind, r.reactor.id, r.emoji]).sort(),
    [['agent', 'pm-x', '🎉'], ['user', OP, '👍']]);
});

test('an agent header without a valid token is rejected with 401, nothing stored, never the operator', async () => {
  const ok = await put('pm-spoof', '👍');
  assert.equal(ok.status, 200);
  const bare = await put('pm-spoof', '😂', { 'x-orchestra-agent': 'pm-x' });
  assert.equal(bare.status, 401);
  assert.deepEqual(bare.json, { error: 'agent_token_invalid' });
  const wrong = await put('pm-spoof', '😮', { 'x-orchestra-agent': 'pm-x', 'x-orchestra-agent-token': 'tok-route-999' });
  assert.equal(wrong.status, 401);
  assert.deepEqual(wrong.json, { error: 'agent_token_invalid' });
  const del = await call('DELETE', { body: { surface: 'agent-chat', conv: 'pm-spoof', ref: REF }, headers: { 'x-orchestra-agent': 'pm-x' } });
  assert.equal(del.status, 401);
  const got = await list('pm-spoof');
  assert.deepEqual(got.reactions[REF].map((r: any) => [r.reactor.kind, r.emoji]), [['user', '👍']],
    'only the operator\'s earlier reaction; the rejected writes stored nothing and removed nothing');
});

test('DELETE removes the caller\'s reaction (body or query), once', async () => {
  await put('pm-del', '👍');
  await put('pm-del', '🎉', AGENT);
  const d1 = await call('DELETE', { body: { surface: 'agent-chat', conv: 'pm-del', ref: REF } });
  assert.equal(d1.status, 200);
  assert.deepEqual([d1.json.removed, d1.json.emoji, d1.json.reactor], [true, null, { kind: 'user', id: OP }]);
  const d2 = await call('DELETE', { body: { surface: 'agent-chat', conv: 'pm-del', ref: REF } });
  assert.equal(d2.json.removed, false);
  const got = await list('pm-del');
  assert.deepEqual(got.reactions[REF].map((r: any) => r.reactor.id), ['pm-x'], 'the agent\'s stays');
  const q = `?surface=agent-chat&conv=pm-del&ref=${encodeURIComponent(REF)}`;
  const d3 = await call('DELETE', { q, headers: AGENT });
  assert.equal(d3.json.removed, true);
  assert.deepEqual((await list('pm-del')).reactions, {});
});

test('bad input is a 400 that says why; nothing is stored', async () => {
  const cases: [Record<string, unknown>, RegExp][] = [
    [{ surface: 'mail', conv: 'pm-bad', ref: REF, emoji: '👍' }, /surface/],
    [{ surface: 'agent-chat', ref: REF, emoji: '👍' }, /conv/],
    [{ surface: 'agent-chat', conv: 'pm-bad', emoji: '👍' }, /ref/],
    [{ surface: 'agent-chat', conv: 'pm-bad', ref: REF, emoji: 'yes' }, /emoji/],
    [{ surface: 'agent-chat', conv: 'pm-bad', ref: REF }, /emoji/],
  ];
  for (const [body, why] of cases) {
    const r = await call('PUT', { body });
    assert.equal(r.status, 400, JSON.stringify(body));
    assert.match(r.json.error, why);
  }
  assert.equal((await call('GET', { q: '?surface=agent-chat' })).status, 400);
  assert.deepEqual((await list('pm-bad')).reactions, {});
});

test('arturo and call surfaces take their numeric refs', async () => {
  const r = await call('PUT', { body: { surface: 'arturo', conv: 'thr_abc', ref: '12', emoji: '👍' } });
  assert.equal(r.status, 200);
  assert.deepEqual((await list('thr_abc', 'arturo')).reactions['12'].map((x: any) => x.emoji), ['👍']);
  const c = await call('PUT', { body: { surface: 'call', conv: 'vc_1', ref: '3', emoji: '🎉' } });
  assert.equal(c.status, 200);
});

test('trusted-proxy mode with no identity: 401, nothing stored', async () => {
  process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = '1';
  try {
    assert.equal((await put('pm-auth', '👍')).status, 401);
    assert.equal((await call('GET', { q: '?surface=agent-chat&conv=pm-auth' })).status, 401);
  } finally { delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS; }
  assert.deepEqual((await list('pm-auth')).reactions, {});
});
