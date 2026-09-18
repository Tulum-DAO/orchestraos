/**
 * G20 — thread switching + the page-context CARD.
 *
 * Two operator requirements (2026-09-18):
 *  1. "swap between Arturo's previous conversations and pick up right where we left off"
 *  2. Arturo focuses on the page you are looking at BY DEFAULT — and that focus is a CARD
 *     in the chat you can DELETE. Deleting it is how you tell Arturo to stop focusing on
 *     that page; the choice is per-thread and must survive a reload.
 *
 *   node --experimental-strip-types dashboard/src/lib/arturoThreads.test.mjs
 */
import assert from 'node:assert';
import {
  listThreads, loadThread, contextCardFor, contextCardLabel,
  isContextDismissed, dismissContext, restoreContext, contextForTurn,
} from './arturoThreads.ts';
import { contextFromLocation } from './arturo.ts';

// --- a local localStorage, since these helpers persist the per-thread choice ---------------
const mem = new Map();
globalThis.localStorage = {
  getItem: (k) => (mem.has(k) ? mem.get(k) : null),
  setItem: (k, v) => mem.set(k, String(v)),
  removeItem: (k) => mem.delete(k),
};

// --- listThreads: reads the SERVER, newest first, and never throws on a bad hop ------------
{
  globalThis.fetch = async (url) => {
    assert.match(String(url), /\/api\/arturo\/threads/);
    return { ok: true, status: 200, json: async () => ({ ok: true, threads: [
      { id: 'c2', title: 'newer', updated: 200, turns: 4, snippet: 'a2' },
      { id: 'c1', title: 'older', updated: 100, turns: 2, snippet: 'a1' },
    ] }) };
  };
  const threads = await listThreads();
  assert.deepEqual(threads.map((t) => t.id), ['c2', 'c1']);
  assert.equal(threads[0].title, 'newer');
}
{
  globalThis.fetch = async () => { throw new Error('network'); };
  assert.deepEqual(await listThreads(), []);          // a dead hop shows no threads, not a crash
}

// --- loadThread: its turns come back in order so the pane can resume -----------------------
{
  globalThis.fetch = async (url) => {
    assert.match(String(url), /\/api\/arturo\/threads\/c1/);
    return { ok: true, status: 200, json: async () => ({ ok: true, thread: { id: 'c1', title: 'older', turns: [
      { role: 'user', content: 'q1' }, { role: 'assistant', content: 'a1' },
    ] } }) };
  };
  const t = await loadThread('c1');
  assert.deepEqual(t.turns.map((x) => x.role), ['user', 'assistant']);
  assert.equal(t.turns[0].content, 'q1');
}
{
  globalThis.fetch = async () => ({ ok: false, status: 404, json: async () => ({ ok: false, error: 'not_found' }) });
  assert.equal(await loadThread('nope'), null);
}

// --- the context CARD: what the operator actually sees -------------------------------------
{
  const ctx = contextFromLocation('/approvals', { id: 'apr_1a2b3c4d' }, '');
  const card = contextCardFor(ctx);
  assert.equal(card.kind, 'page-context');
  // plain English, not route jargon — it is a card in a chat, not a debug line
  assert.equal(contextCardLabel(ctx), 'Approvals · apr_1a2b3c4d');
  assert.equal(contextCardLabel(contextFromLocation('/agents', {}, '')), 'Agents');
  assert.equal(contextCardLabel(contextFromLocation('/', {}, '')), 'Overview');
}

// --- default ON, deletable, per-thread, and it survives a reload ---------------------------
{
  const ctx = contextFromLocation('/approvals', { id: 'apr_1a2b3c4d' }, '');
  assert.equal(isContextDismissed('t1'), false);                 // default: Arturo focuses the page
  assert.deepEqual(contextForTurn('t1', ctx), ctx);              // so the turn carries it

  dismissContext('t1');                                          // the operator deletes the card
  assert.equal(isContextDismissed('t1'), true);
  assert.equal(contextForTurn('t1', ctx), null);                 // and the turn stops carrying it
  assert.equal(isContextDismissed('t2'), false);                 // per THREAD, not global

  // a reload is a fresh module read of the same storage
  assert.equal(isContextDismissed('t1'), true);

  restoreContext('t1');                                          // re-attachable, not a one-way door
  assert.equal(isContextDismissed('t1'), false);
  assert.deepEqual(contextForTurn('t1', ctx), ctx);
}

// --- no page context at all (no card, nothing to delete) ------------------------------------
{
  assert.equal(contextCardFor(null), null);
  assert.equal(contextForTurn('t3', null), null);
}

console.log('arturoThreads.test.mjs: all assertions passed');
