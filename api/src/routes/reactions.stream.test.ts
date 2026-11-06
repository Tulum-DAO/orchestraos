/**
 * GET /api/reactions/stream?surface=&conv= (spec "SSE"): `event: reaction` {surface, conv, ref, reactor, emoji|null,
 * at} for that conversation only, on set and on removal; a closed client stops listening.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/routes/reactions.stream.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';

const dir = testTmpDir('reactions-stream-');
process.env.ORCHESTRA_DIR = dir;
process.env.ORCHESTRA_AGENT_REACT_TOKEN_FILE = join(dir, 'agent-react-token');
writeFileSync(process.env.ORCHESTRA_AGENT_REACT_TOKEN_FILE, 'tok-stream-1\n', { mode: 0o600 });
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;

const express = (await import('express')).default;
const { createReactionsRouter } = await import('./reactions.js');
const { ReactionStore } = await import('../lib/reactions-store.js');
const { reactionListenerCount } = await import('../services/reaction-bus.js');
const { loadConfig } = await import('../lib/config.js');
const OP = loadConfig().operatorId;

const store = new ReactionStore(join(dir, 'state', 'reactions.db'));
const app = express();
app.use(express.json());
app.use('/api/reactions', createReactionsRouter({ store: () => store }));
const server = app.listen(0);
after(() => { server.closeAllConnections?.(); server.close(); store.close(); rmSync(dir, { recursive: true, force: true }); });
const base = `http://127.0.0.1:${(server.address() as any).port}/api/reactions`;
const REF = '11111111-2222-4333-8444-000000000001~a1#1';

const put = (body: Record<string, unknown>, headers: Record<string, string> = {}) =>
  fetch(base, { method: 'PUT', headers: { 'content-type': 'application/json', ...headers }, body: JSON.stringify(body) });

async function waitFor(cond: () => boolean, ms = 2000): Promise<boolean> {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (cond()) return true;
    await new Promise((ok) => setTimeout(ok, 25));
  }
  return cond();
}

/** An open stream: read frames until a predicate holds (or time is up). ONE read in flight at a time. */
async function openStream(q: string) {
  const ctl = new AbortController();
  const res = await fetch(`${base}/stream?${q}`, { signal: ctl.signal });
  const reader = res.body!.getReader();
  const got: { event: string; data: any }[] = [];
  let buf = '';
  let pending: Promise<ReadableStreamReadResult<Uint8Array>> | null = null;
  async function until(pred: (f: typeof got) => boolean, ms = 3000) {
    const deadline = Date.now() + ms;
    while (Date.now() < deadline && !pred(got)) {
      pending ??= reader.read();
      const r = await Promise.race([pending, new Promise<null>((ok) => setTimeout(() => ok(null), 100))]);
      if (r) pending = null;
      if (r && !r.done) buf += Buffer.from(r.value).toString('utf-8');
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        const event = /^event: (.*)$/m.exec(block)?.[1];
        const data = /^data: (.*)$/m.exec(block)?.[1];
        if (event && data) got.push({ event, data: JSON.parse(data) });
      }
    }
    return got;
  }
  const close = () => { pending?.catch(() => { /* aborted */ }); ctl.abort(); };
  return { res, until, close };
}

test('a reaction reaches the stream of its own conversation only, as set and as removed', async () => {
  const s = await openStream('surface=agent-chat&conv=pm-a');
  try {
    assert.equal(s.res.status, 200);
    assert.match(s.res.headers.get('content-type') ?? '', /text\/event-stream/);
    await put({ surface: 'agent-chat', conv: 'pm-b', ref: REF, emoji: '🎉' });          // another conversation
    await put({ surface: 'arturo', conv: 'pm-a', ref: '4', emoji: '🎉' });               // same conv id, other surface
    await put({ surface: 'agent-chat', conv: 'pm-a', ref: REF, emoji: '👍' });
    await put({ surface: 'agent-chat', conv: 'pm-a', ref: REF, emoji: '👍' });           // same again: removed
    const frames = (await s.until((f) => f.filter((x) => x.event === 'reaction').length >= 2))
      .filter((x) => x.event === 'reaction').map((x) => x.data);
    assert.deepEqual(frames.map((d) => [d.surface, d.conv, d.ref, d.reactor.kind, d.reactor.id, d.emoji]), [
      ['agent-chat', 'pm-a', REF, 'user', OP, '👍'],
      ['agent-chat', 'pm-a', REF, 'user', OP, null],
    ]);
    assert.match(frames[0].at, /^\d{4}-\d{2}-\d{2}T/);
  } finally { s.close(); }
});

test('an agent\'s reaction (token-proven) is on the stream as the agent', async () => {
  const s = await openStream('surface=agent-chat&conv=pm-c');
  try {
    await put({ surface: 'agent-chat', conv: 'pm-c', ref: REF, emoji: '❤️' },
      { 'x-orchestra-agent': 'pm-x', 'x-orchestra-agent-token': 'tok-stream-1' });
    const f = (await s.until((g) => g.some((x) => x.event === 'reaction'))).find((x) => x.event === 'reaction');
    assert.deepEqual(f?.data.reactor, { kind: 'agent', id: 'pm-x' });
  } finally { s.close(); }
});

test('a DELETE that removed something is on the stream; one that removed nothing is not', async () => {
  await put({ surface: 'agent-chat', conv: 'pm-d', ref: REF, emoji: '😮' });
  const s = await openStream('surface=agent-chat&conv=pm-d');
  try {
    const del = () => fetch(base, { method: 'DELETE', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ surface: 'agent-chat', conv: 'pm-d', ref: REF }) });
    await del();
    await del();
    const frames = (await s.until(() => false, 800)).filter((x) => x.event === 'reaction');
    assert.equal(frames.length, 1);
    assert.equal(frames[0].data.emoji, null);
  } finally { s.close(); }
});

test('a closed stream stops listening; a bad query is a 400, not a stream', async () => {
  await waitFor(() => reactionListenerCount() === 0); // earlier tests' closed streams may still be unwinding
  const before = reactionListenerCount();
  const s = await openStream('surface=call&conv=vc_1');
  assert.equal(reactionListenerCount(), before + 1);
  s.close();
  assert.ok(await waitFor(() => reactionListenerCount() === before), 'listener removed after the client left');
  const bad = await fetch(`${base}/stream?surface=agent-chat`);
  assert.equal(bad.status, 400);
  assert.match(((await bad.json()) as any).error, /conv/);
});
