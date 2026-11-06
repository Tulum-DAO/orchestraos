import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  applyThreadEdit, commitDelete, commitRename, normalizeTitle, renameThread, subscribeThreadEdits, threadFacts,
  threadLabel, titleTooLong, THREAD_TITLE_MAX, type ThreadEdit,
} from './arturoThreadEdits.ts';
import { currentThread } from './arturoCurrent.ts';

type Call = { url: string; method: string; body?: string };

async function withFetch(reply: (c: Call) => { status: number; body: unknown }, f: (calls: Call[]) => Promise<void>) {
  const real = globalThis.fetch;
  const calls: Call[] = [];
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    const c = { url, method: init?.method || 'GET', body: init?.body ? String(init.body) : undefined };
    calls.push(c);
    const r = reply(c);
    return new Response(JSON.stringify(r.body), { status: r.status });
  }) as typeof fetch;
  try { await f(calls); } finally { globalThis.fetch = real; }
}

function heard(): { edits: ThreadEdit[]; off: () => void } {
  const edits: ThreadEdit[] = [];
  const off = subscribeThreadEdits((e) => edits.push(e));
  return { edits, off };
}

const list = () => [{ id: 'a', title: 'First' }, { id: 'b', title: 'Second' }];

test('a title is one line, trimmed, at most 80 characters as the server counts them', () => {
  assert.equal(normalizeTitle('  Two\n  lines\there '), 'Two lines here');
  assert.equal(titleTooLong('x'.repeat(THREAD_TITLE_MAX)), false);
  assert.equal(titleTooLong('x'.repeat(THREAD_TITLE_MAX + 1)), true);
  assert.equal(titleTooLong('😀'.repeat(THREAD_TITLE_MAX)), false);   // one character each, not two
});

test('a chat with no title yet shows as Untitled', () => {
  assert.equal(threadLabel({ title: '' }), 'Untitled');
  assert.equal(threadLabel({ title: 'Getting started' }), 'Getting started');
});

test('a rename changes that chat only; a delete drops it; a list without it is left as it was', () => {
  const l = list();
  assert.deepEqual(applyThreadEdit(l, { kind: 'rename', id: 'b', title: 'Renamed', custom: true }),
    [{ id: 'a', title: 'First' }, { id: 'b', title: 'Renamed', title_custom: true }]);
  assert.deepEqual(applyThreadEdit(l, { kind: 'delete', id: 'a' }), [{ id: 'b', title: 'Second' }]);
  assert.equal(applyThreadEdit(l, { kind: 'delete', id: 'zzz' }), l);
});

test('rename PATCHes the cleaned title and answers with the title the server now shows', async () => {
  await withFetch(() => ({ status: 200, body: { ok: true, thread: { id: 'a b', title: 'New name', title_custom: true } } }), async (calls) => {
    assert.deepEqual(await renameThread('a b', '  New \n name '), { ok: true, title: 'New name', custom: true });
    assert.deepEqual(calls, [{ url: '/api/arturo/threads/a%20b', method: 'PATCH', body: '{"title":"New name"}' }]);
  });
});

test('a too-long name is refused here, without asking the server', async () => {
  await withFetch(() => ({ status: 200, body: {} }), async (calls) => {
    const r = await renameThread('a', 'x'.repeat(81));
    assert.equal(r.ok, false);
    assert.deepEqual(calls, []);
  });
});

test('commitRename shows the new title at once, then the server\'s', async () => {
  const { edits, off } = heard();
  try {
    await withFetch(() => ({ status: 200, body: { ok: true, thread: { id: 'a', title: 'New', title_custom: true } } }), async () => {
      assert.equal(await commitRename('a', 'First', ' New '), null);
    });
    assert.deepEqual(edits, [{ kind: 'rename', id: 'a', title: 'New', custom: true },
                             { kind: 'rename', id: 'a', title: 'New', custom: true }]);
  } finally { off(); }
});

test('commitRename puts the old title back and says why when the server refuses', async () => {
  const { edits, off } = heard();
  try {
    await withFetch(() => ({ status: 404, body: { ok: false, error: 'no such thread' } }), async () => {
      assert.equal(await commitRename('a', 'First', 'New'), 'no such thread');
    });
    assert.deepEqual(edits.map((e) => (e.kind === 'rename' ? e.title : e.kind)), ['New', 'First']);
  } finally { off(); }
});

test('commitRename with an unreachable server rolls back too', async () => {
  const { edits, off } = heard();
  const real = globalThis.fetch;
  globalThis.fetch = (async () => { throw new Error('down'); }) as typeof fetch;
  try {
    assert.equal(await commitRename('a', 'First', 'New'), "Couldn't reach the server");
    assert.deepEqual(edits.map((e) => (e.kind === 'rename' ? e.title : e.kind)), ['New', 'First']);
  } finally { globalThis.fetch = real; off(); }
});

test('an empty name waits for the server, which puts the auto-title back', async () => {
  const { edits, off } = heard();
  try {
    await withFetch(() => ({ status: 200, body: { ok: true, thread: { id: 'a', title: 'How many agents?', title_custom: false } } }), async (calls) => {
      assert.equal(await commitRename('a', 'My name', '   '), null);
      assert.equal(calls[0].body, '{"title":""}');
    });
    assert.deepEqual(edits, [{ kind: 'rename', id: 'a', title: 'How many agents?', custom: false }]);
  } finally { off(); }
});

test('the same title again asks nothing', async () => {
  await withFetch(() => ({ status: 200, body: {} }), async (calls) => {
    assert.equal(await commitRename('a', 'First', ' First '), null);
    assert.deepEqual(calls, []);
  });
});

test('the delete dialog reads the facts learned in that chat', async () => {
  await withFetch(() => ({ status: 200, body: { ok: true, onboarding: false,
    facts: [{ store: 'operator', key: 'timezone', label: 'timezone', value: 'Europe/Lisbon' }, { bad: 1 }] } }), async (calls) => {
    const r = await threadFacts('c1');
    assert.equal(calls[0].url, '/api/arturo/threads/c1?facts=1');
    assert.deepEqual(r, { onboarding: false, facts: [{ store: 'operator', key: 'timezone', label: 'timezone', value: 'Europe/Lisbon' }] });
  });
});

function withStorage(f: () => Promise<void>) {
  const g = globalThis as unknown as { localStorage?: Storage };
  const before = g.localStorage;
  const m = new Map<string, string>();
  g.localStorage = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); } } as Storage;
  return f().finally(() => { g.localStorage = before; });
}

test('delete is published only once it happened, and the open chat moves to a fresh one', async () => {
  await withStorage(async () => {
    localStorage.setItem('orchestra.arturo.conversation', 'c1');
    const { edits, off } = heard();
    try {
      await withFetch(() => ({ status: 200, body: { ok: true, deleted: 'c1', facts_forgotten: 1 } }), async (calls) => {
        assert.equal(await commitDelete('c1', true, () => 'web_fresh'), null);
        assert.deepEqual(calls, [{ url: '/api/arturo/threads/c1?forget_facts=1', method: 'DELETE', body: undefined }]);
      });
      assert.deepEqual(edits, [{ kind: 'delete', id: 'c1' }]);
      assert.equal(currentThread(), 'web_fresh');
    } finally { off(); }
  });
});

test('deleting another chat leaves the open one; a refused delete changes nothing', async () => {
  await withStorage(async () => {
    localStorage.setItem('orchestra.arturo.conversation', 'open');
    const { edits, off } = heard();
    try {
      await withFetch(() => ({ status: 200, body: { ok: true } }), async (calls) => {
        assert.equal(await commitDelete('other', false, () => 'web_fresh'), null);
        assert.equal(calls[0].url, '/api/arturo/threads/other');
      });
      assert.equal(currentThread(), 'open');
      await withFetch(() => ({ status: 403, body: { ok: false, error: 'Only an admin can delete a chat' } }), async () => {
        assert.equal(await commitDelete('open', false, () => 'web_fresh'), 'Only an admin can delete a chat');
      });
      assert.equal(currentThread(), 'open');
      assert.deepEqual(edits, [{ kind: 'delete', id: 'other' }]);
    } finally { off(); }
  });
});

test('a refused rename puts back whether the old title was a chosen one', async () => {
  const { edits, off } = heard();
  try {
    await withFetch(() => ({ status: 500, body: { ok: false, error: 'could not save the title' } }), async () => {
      assert.equal(await commitRename('a', 'Auto title', 'New', false), 'could not save the title');
    });
    assert.deepEqual(edits[1], { kind: 'rename', id: 'a', title: 'Auto title', custom: false });
  } finally { off(); }
});

test('an older rename answering after a newer one was asked never puts its title back', async () => {
  const { edits, off } = heard();
  const real = globalThis.fetch;
  const pending: ((r: Response) => void)[] = [];
  globalThis.fetch = (() => new Promise<Response>((res) => { pending.push(res); })) as typeof fetch;
  try {
    const first = commitRename('a', 'A', 'B');
    const second = commitRename('a', 'B', 'C');
    await new Promise((r) => setTimeout(r, 0));
    pending[1](new Response(JSON.stringify({ ok: true, thread: { id: 'a', title: 'C', title_custom: true } }), { status: 200 }));
    await second;
    pending[0](new Response(JSON.stringify({ ok: false, error: 'busy' }), { status: 409 }));
    assert.equal(await first, 'busy');
    const titles = edits.map((e) => (e.kind === 'rename' ? e.title : e.kind));
    assert.deepEqual(titles, ['B', 'C', 'C']);           // no rollback to A after C
  } finally { globalThis.fetch = real; off(); }
});
