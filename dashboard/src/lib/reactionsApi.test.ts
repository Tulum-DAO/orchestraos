// The dashboard's side of /api/reactions (lib/reactionsApi.ts): GET read into a map, PUT answered or refused, the
// viewer from /api/me, and one shared stream per conversation that says when it came back.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fetchReactions, loadMe, parseChange, putReaction, resetMe, subscribeReactions } from './reactionsApi.ts';

type Call = { url: string; init?: RequestInit };
function fakeFetch(answer: (c: Call) => { status?: number; body?: unknown } | Error) {
  const calls: Call[] = [];
  const fn = (async (url: string, init?: RequestInit) => {
    const c = { url, init };
    calls.push(c);
    const a = answer(c);
    if (a instanceof Error) throw a;
    return new Response(JSON.stringify(a.body ?? {}), { status: a.status ?? 200, headers: { 'Content-Type': 'application/json' } });
  }) as unknown as typeof fetch;
  return { fn, calls };
}

const sam = { kind: 'user', id: 'sam' } as const;
const REF = 's1~11111111-2222-4333-8444-00000000006b#0';

test('GET: the conversation\'s reactions by ref; malformed rows dropped; a refusal throws', async () => {
  const { fn, calls } = fakeFetch(() => ({ body: { surface: 'agent-chat', conv: 'pm-x', reactions: {
    [REF]: [{ emoji: '👍', reactor: sam, at: '2026-11-02T10:00:00Z' }, { emoji: '', reactor: sam }, { emoji: '🎉', reactor: { kind: 'robot', id: 'x' } }],
    'empty': [],
  } } }));
  const m = await fetchReactions('agent-chat', 'pm-x', fn);
  assert.equal(calls[0].url, '/api/reactions?surface=agent-chat&conv=pm-x');
  assert.deepEqual(m, { [REF]: [{ emoji: '👍', reactor: sam, at: '2026-11-02T10:00:00Z' }] });
  await assert.rejects(fetchReactions('agent-chat', 'pm-x', fakeFetch(() => ({ status: 404, body: { error: 'nope' } })).fn));
});

test('PUT: sends surface, conv, ref, emoji; answers the server\'s change, or its error, never throws', async () => {
  const ok = fakeFetch(() => ({ body: { ok: true, surface: 'agent-chat', conv: 'pm-x', ref: REF, reactor: sam, emoji: '👍', at: 't' } }));
  const r = await putReaction('agent-chat', 'pm-x', REF, '👍', ok.fn);
  assert.equal(ok.calls[0].url, '/api/reactions');
  assert.equal(ok.calls[0].init?.method, 'PUT');
  assert.deepEqual(JSON.parse(String(ok.calls[0].init?.body)), { surface: 'agent-chat', conv: 'pm-x', ref: REF, emoji: '👍' });
  assert.deepEqual(r, { ok: true, change: { surface: 'agent-chat', conv: 'pm-x', ref: REF, reactor: sam, emoji: '👍', at: 't' } });
  assert.deepEqual(await putReaction('agent-chat', 'pm-x', REF, '👍', fakeFetch(() => ({ status: 400, body: { error: 'emoji must be a single emoji' } })).fn),
    { ok: false, error: 'emoji must be a single emoji' });
  assert.deepEqual(await putReaction('agent-chat', 'pm-x', REF, '👍', fakeFetch(() => ({ status: 500, body: {} })).fn),
    { ok: false, error: "Couldn't save the reaction (HTTP 500)" });
  assert.deepEqual(await putReaction('agent-chat', 'pm-x', REF, '👍', fakeFetch(() => new Error('down')).fn),
    { ok: false, error: "Couldn't reach the server" });
});

test('a change: a ref, a reactor, an emoji or null (removed); anything else is not one', () => {
  assert.deepEqual(parseChange({ ref: REF, reactor: sam, emoji: null, at: 't' }), { ref: REF, reactor: sam, emoji: null, at: 't' });
  for (const bad of [null, {}, { ref: REF, reactor: sam, emoji: '' }, { ref: '', reactor: sam, emoji: '👍' }, { ref: REF, reactor: { kind: 'x', id: 'y' }, emoji: '👍' }]) {
    assert.equal(parseChange(bad), null, JSON.stringify(bad));
  }
});

test('the viewer is /api/me\'s username, read once; a failure is retried next time', async () => {
  resetMe();
  let n = 0;
  const { fn } = fakeFetch(() => (++n === 1 ? new Error('down') : { body: { username: 'sam', role: 'admin' } }));
  assert.equal(await loadMe(fn), null);
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(await loadMe(fn), sam);
  assert.deepEqual(await loadMe(fn), sam);
  assert.equal(n, 2);
  resetMe();
});

class FakeES {
  static all: FakeES[] = [];
  url: string; closed = false; readyState = 1; onopen: ((e: Event) => void) | null = null; onerror: ((e: Event) => void) | null = null;
  private ls = new Map<string, ((e: Event) => void)[]>();
  constructor(url: string) { this.url = url; FakeES.all.push(this); }
  addEventListener(t: string, f: (e: Event) => void) { this.ls.set(t, [...(this.ls.get(t) ?? []), f]); }
  close() { this.closed = true; }
  emit(t: string, data: string) { (this.ls.get(t) ?? []).forEach((f) => f({ data } as unknown as Event)); }
}

test('one stream per conversation for every reader, closed with the last; frames parsed; a comeback says so', () => {
  FakeES.all = [];
  const got: string[] = [];
  const reopened: string[] = [];
  const ES = FakeES as unknown as Parameters<typeof subscribeReactions>[3];
  const offA = subscribeReactions('agent-chat', 'pm-x', { onChange: (c) => got.push(`a:${c.emoji}`), onReopen: () => reopened.push('a') }, ES);
  const offB = subscribeReactions('agent-chat', 'pm-x', { onChange: (c) => got.push(`b:${c.emoji}`) }, ES);
  assert.equal(FakeES.all.length, 1);
  const es = FakeES.all[0];
  assert.equal(es.url, '/api/reactions/stream?surface=agent-chat&conv=pm-x');
  es.emit('reaction', JSON.stringify({ surface: 'agent-chat', conv: 'pm-x', ref: REF, reactor: sam, emoji: '❤️', at: 't' }));
  es.emit('reaction', 'not json');
  assert.deepEqual(got, ['a:❤️', 'b:❤️']);
  es.onopen?.({} as Event);                 // the first open is not a comeback
  assert.deepEqual(reopened, []);
  es.onerror?.({} as Event);
  es.onopen?.({} as Event);
  assert.deepEqual(reopened, ['a']);
  offA();
  assert.equal(es.closed, false);
  offB();
  assert.equal(es.closed, true);
  subscribeReactions('agent-chat', 'pm-x', { onChange: () => {} }, ES)();
  assert.equal(FakeES.all.length, 2);       // a new reader after the last left opens a new stream
  assert.doesNotThrow(() => subscribeReactions('agent-chat', 'pm-x', { onChange: () => {} }, undefined)());
});

test('a stream the browser gave up on (readyState CLOSED) leaves the shared map and tells its readers', () => {
  FakeES.all = [];
  const ES = FakeES as unknown as Parameters<typeof subscribeReactions>[3];
  const closed: string[] = [];
  subscribeReactions('agent-chat', 'pm-y', { onChange: () => {}, onClosed: () => closed.push('a') }, ES);
  subscribeReactions('agent-chat', 'pm-y', { onChange: () => {}, onClosed: () => closed.push('b') }, ES);
  const es = FakeES.all[0];
  es.readyState = 2;
  es.onerror?.({} as Event);
  assert.deepEqual(closed, ['a', 'b']);
  assert.equal(es.closed, true);
  subscribeReactions('agent-chat', 'pm-y', { onChange: () => {} }, ES)();   // the next reader gets a fresh stream
  assert.equal(FakeES.all.length, 2);
  // a plain error (the browser is retrying) is not a close
  const es2 = FakeES.all[1];
  subscribeReactions('agent-chat', 'pm-y', { onChange: () => {}, onClosed: () => closed.push('c') }, ES);
  es2.onerror?.({} as Event);
  assert.deepEqual(closed, ['a', 'b']);
});
