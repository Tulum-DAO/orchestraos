import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadThreadPage, threadFromJson } from './arturoThreads';

test("a loaded thread keeps its last_brain, so reopening it restores the brain", () => {
  const t = threadFromJson({ id: 'c1', title: 'q', turns: [{ role: 'user', content: 'hi' }],
                             last_brain: { provider: 'codex', model: 'gpt-5.6-terra' } });
  assert.deepEqual(t?.last_brain, { provider: 'codex', model: 'gpt-5.6-terra' });
  assert.equal(t?.turns.length, 1);
});

test('a thread from before the migration (no last_brain) reads as the default brain', () => {
  assert.equal(threadFromJson({ id: 'old', title: '', turns: [] })?.last_brain, null);
});

test('no thread is null', () => {
  assert.equal(threadFromJson(null), null);
});

// --- a long thread read a page at a time (GET /api/arturo/threads/:id?before=&limit=) ---

async function withFetch(body: unknown, f: (urls: string[]) => Promise<void>) {
  const real = globalThis.fetch;
  const urls: string[] = [];
  globalThis.fetch = (async (url: string) => { urls.push(url); return new Response(JSON.stringify(body), { status: 200 }); }) as typeof fetch;
  try { await f(urls); } finally { globalThis.fetch = real; }
}
const turn = (seq: number) => ({ role: 'user', content: `turn ${seq}`, seq });

test('the newest page, then an older one by its cursor', async () => {
  await withFetch({ ok: true, thread: { id: 'c1', turns: [turn(101), turn(102)] }, has_older: true, oldest_seq: 101 }, async (urls) => {
    const p = await loadThreadPage('c1');
    assert.equal(urls[0], '/api/arturo/threads/c1?limit=100');
    assert.deepEqual([p?.hasOlder, p?.next, p?.thread?.turns.length], [true, 101, 2]);
  });
  await withFetch({ ok: true, thread: { id: 'c1', turns: [turn(1), turn(2)] }, has_older: false, oldest_seq: 1 }, async (urls) => {
    const p = await loadThreadPage('c1', 101);
    assert.equal(urls[0], '/api/arturo/threads/c1?limit=100&before=101');
    assert.deepEqual([p?.hasOlder, p?.next], [false, 1]);
  });
});

test('a server that does not page answers the whole thread: shown whole, nothing older to read', async () => {
  await withFetch({ ok: true, thread: { id: 'c1', turns: [{ role: 'user', content: 'all of it' }] } }, async () => {
    const p = await loadThreadPage('c1');
    assert.deepEqual([p?.hasOlder, p?.next, p?.thread?.turns.length], [false, null, 1]);
  });
});

test('an older page that is not older than its cursor (a hop that dropped it), or an unknown cursor, is refused', async () => {
  await withFetch({ ok: true, thread: { id: 'c1', turns: [turn(101), turn(102)] }, has_older: true, oldest_seq: 101 }, async () => {
    assert.equal(await loadThreadPage('c1', 101), null);
  });
  await withFetch({ ok: true, thread: { id: 'c1', turns: [{ role: 'user', content: 'whole' }] } }, async () => {
    assert.equal(await loadThreadPage('c1', 101), null);
  });
  await withFetch({ ok: true, thread: { id: 'c1', turns: [] }, has_older: false, oldest_seq: null, error: 'unknown cursor' }, async () => {
    assert.equal(await loadThreadPage('c1', 5), null);
  });
});
