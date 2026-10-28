// The composer's send, end to end against a fake server: what the box does for each answer.
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { sendFromComposer } from './agentSendFlow.ts';
import { COULDNT_CONFIRM } from './agentSend.ts';

const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; });

type Reply = { status: number; body: unknown } | 'throw';
function serve(send: Reply, transcript: Reply = { status: 200, body: { items: [] } }) {
  const calls: string[] = [];
  globalThis.fetch = (async (url: string) => {
    calls.push(String(url));
    const r = String(url).includes('/transcript') ? transcript : send;
    if (r === 'throw') throw new TypeError('Failed to fetch');
    return new Response(JSON.stringify(r.body), { status: r.status });
  }) as typeof fetch;
  return calls;
}
const go = (prior?: Set<string>) => sendFromComposer('pm-demo', { text: 'hello there', attachments: [] }, { force: false, priorRowIds: prior });

test('delivered to an idle agent: ok, and a plain pending bubble with the text the agent got', async () => {
  serve({ status: 200, body: { state: 'delivered', agent_state: 'idle', sent_text: 'hello there' } });
  const r = await go();
  assert.equal(r.ok, true);
  assert.deepEqual(r.pending, { text: 'hello there', kind: 'sent', pointer: undefined });
});

test('delivered mid-turn: the bubble gets the queued chip and the note says it waits for the turn', async () => {
  serve({ status: 200, body: { state: 'delivered', agent_state: 'working', sent_text: 'hello there' } });
  const r = await go();
  assert.equal(r.pending?.kind, 'queuedInTurn');
  assert.match(r.note || '', /turn ends/);
});

test('held: not "ok" (no sent tick), but it leaves the box as an amber held bubble', async () => {
  serve({ status: 200, body: { state: 'held', reason: 'busy_working', sent_text: 'hello there' } });
  const r = await go();
  assert.equal(r.ok, false);
  assert.equal(r.held, true);
  assert.equal(r.pending?.kind, 'held');
});

test('composer hold (409): refused, the text stays, no bubble', async () => {
  serve({ status: 409, body: { busy: true, composer_text: 'draft', payload: { text: 'hello there' } } });
  const r = await go();
  assert.equal(r.refused, true);
  assert.equal(r.pending, undefined);
});

test('unknown + the transcript has it (a NEW row): treated as delivered', async () => {
  serve({ status: 502, body: { state: 'unknown', reason: 'unverified_submit', sent_text: 'hello there' } },
    { status: 200, body: { items: [{ kind: 'text', role: 'user', uuid: 'u9', text: 'hello there' }] } });
  const r = await go(new Set(['u1']));
  assert.equal(r.ok, true);
  assert.equal(r.pending?.kind, 'sent');
});

test('unknown + only an OLDER identical row: not evidence -> "couldn\'t confirm", text kept', async () => {
  serve({ status: 502, body: { state: 'unknown', reason: 'unverified_submit' } },
    { status: 200, body: { items: [{ kind: 'text', role: 'user', uuid: 'u1', text: 'hello there' }] } });
  const r = await go(new Set(['u1']));
  assert.equal(r.ok, false);
  assert.equal(r.note, COULDNT_CONFIRM);
  assert.equal(r.pending, undefined);
});

test('unknown + the transcript cannot be read: "couldn\'t confirm", never "not sent"', async () => {
  serve({ status: 502, body: { state: 'unknown', reason: 'unverified_submit' } }, { status: 500, body: {} });
  const r = await go();
  assert.equal(r.note, COULDNT_CONFIRM);
  assert.doesNotMatch(r.note || '', /not sent/i);
});

test('the connection drops mid-request but the transcript shows it: delivered', async () => {
  let n = 0;
  globalThis.fetch = (async (url: string) => {
    if (String(url).includes('/transcript')) return new Response(JSON.stringify({ items: [{ kind: 'text', role: 'user', uuid: 'u5', text: 'hello there' }] }));
    n += 1; throw new TypeError('Failed to fetch');
  }) as typeof fetch;
  const r = await go(new Set());
  assert.equal(n, 1);
  assert.equal(r.ok, true);
});

test('a definite server failure (durable write failed): not delivered, no bubble', async () => {
  serve({ status: 502, body: { state: 'held', reason: 'durable_write_failed', error: 'Could not store the message — it was not delivered.' } });
  const r = await go();
  assert.equal(r.ok, false);
  assert.equal(r.pending, undefined);
  assert.match(r.note || '', /not delivered/);
});
