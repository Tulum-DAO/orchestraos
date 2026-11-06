/**
 * The reactions store (<data dir>/state/reactions.db, WAL). One row per reactor per message (the PRIMARY KEY): a new
 * pick replaces the old one, the same emoji again removes it, many reactors sit side by side.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/lib/reactions-store.test.ts   (from api/)
 */
import { testTmpDir } from './testTmp.js';
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import { ReactionStore, reactionStore, reactionsDbPath, type ReactionKey, type Reactor } from './reactions-store.js';

const dir = testTmpDir('reactions-store-');
const path = join(dir, 'state', 'reactions.db');
const store = new ReactionStore(path);
after(() => { store.close(); rmSync(dir, { recursive: true, force: true }); });

const REF = '11111111-2222-4333-8444-000000000001~u1#0';
const op = { kind: 'user' as const, id: 'operator' };
const key = (conv: string, reactor: Reactor = op, ref = REF): ReactionKey => ({ surface: 'agent-chat', conv, ref, reactor });

test('the file is made under state/, in WAL mode, with the spec table', () => {
  assert.ok(existsSync(path));
  const raw = new Database(path, { readonly: true });
  try {
    assert.equal(raw.pragma('journal_mode', { simple: true }), 'wal');
    const cols = (raw.prepare('PRAGMA table_info(reactions)').all() as { name: string; pk: number }[]);
    assert.deepEqual(cols.map((c) => c.name), ['surface', 'conv', 'ref', 'reactor_kind', 'reactor_id', 'emoji', 'at']);
    assert.deepEqual(cols.filter((c) => c.pk > 0).map((c) => c.name),
      ['surface', 'conv', 'ref', 'reactor_kind', 'reactor_id']);
  } finally { raw.close(); }
});

test('one emoji per reactor per message: a new pick replaces it', () => {
  const a = store.toggle(key('pm-one'), '👍', '2026-11-02T10:00:00.000Z');
  assert.deepEqual(a, { surface: 'agent-chat', conv: 'pm-one', ref: REF, reactor: op, emoji: '👍', at: '2026-11-02T10:00:00.000Z' });
  const b = store.toggle(key('pm-one'), '❤️', '2026-11-02T10:00:01.000Z');
  assert.equal(b.emoji, '❤️');
  const got = store.list('agent-chat', 'pm-one');
  assert.deepEqual(Object.keys(got), [REF]);
  assert.deepEqual(got[REF], [{ emoji: '❤️', reactor: op, at: '2026-11-02T10:00:01.000Z' }]);
});

test('the same emoji again removes it', () => {
  store.toggle(key('pm-toggle'), '👍');
  const off = store.toggle(key('pm-toggle'), '👍');
  assert.equal(off.emoji, null);
  assert.deepEqual(store.list('agent-chat', 'pm-toggle'), {});
});

test('many reactors on one message sit side by side, grouped by ref', () => {
  const agent = { kind: 'agent' as const, id: 'pm-x' };
  store.toggle(key('pm-many'), '👍', '2026-11-02T10:00:00.000Z');
  store.toggle(key('pm-many', agent), '🎉', '2026-11-02T10:00:02.000Z');
  store.toggle(key('pm-many', op, 'S~u2#0'), '😂', '2026-11-02T10:00:03.000Z');
  const got = store.list('agent-chat', 'pm-many');
  assert.deepEqual(got[REF].map((r) => [r.reactor.kind, r.reactor.id, r.emoji]),
    [['user', 'operator', '👍'], ['agent', 'pm-x', '🎉']]);
  assert.deepEqual(got['S~u2#0'].map((r) => r.emoji), ['😂']);
  assert.deepEqual(store.list('agent-chat', 'other-conv'), {}, 'conversations never mix');
  assert.deepEqual(store.list('arturo', 'pm-many'), {}, 'nor surfaces');
});

test('remove: the reactor\'s row goes, once', () => {
  store.toggle(key('pm-del'), '👍');
  const gone = store.remove(key('pm-del'));
  assert.equal(gone?.emoji, null);
  assert.equal(store.remove(key('pm-del')), null, 'nothing left to remove');
  assert.deepEqual(store.list('agent-chat', 'pm-del'), {});
});

test('the table refuses a reactor kind that is not user/agent', () => {
  const raw = new Database(path);
  try {
    assert.throws(() => raw.prepare(`INSERT INTO reactions VALUES ('agent-chat','c','r','robot','x','👍','t')`).run(),
      /CHECK constraint failed/);
  } finally { raw.close(); }
});

test('reactionsDbPath and the shared store', () => {
  assert.equal(reactionsDbPath('/data'), join('/data', 'state', 'reactions.db'));
  const p = join(dir, 'shared', 'reactions.db');
  const s1 = reactionStore(p);
  assert.equal(reactionStore(p), s1, 'one handle per path');
  s1.close();
});
