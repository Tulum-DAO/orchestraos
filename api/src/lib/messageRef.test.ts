/**
 * The server's message ref (spec "Message refs"): `<session>~<uuid>#<part>`, part = the item's index among the
 * items sharing its uuid (all blocks of one Claude line share o.uuid). Synthetic rows and items with no uuid get none.
 *
 * Run: npx tsx --test src/lib/messageRef.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { refable, partAt, messageRef, stampRefs } from './messageRef.js';

const S = '11111111-2222-4333-8444-000000000001';

test('refable: a real uuid (string, or a codex ordinal number) yes; none or synthetic no', () => {
  assert.equal(refable({ kind: 'text', uuid: 'u1' }), true);
  assert.equal(refable({ kind: 'text', uuid: 7 }), true, 'codex ordinal');
  for (const it of [{ kind: 'text' }, { kind: 'text', uuid: '' }, { uuid: 'rot:x', kind: 'rotation' },
    { uuid: 'queued:1' }, { uuid: 'interrupted:x1' }, { uuid: 'batch:9' }, { kind: 'compaction', uuid: 'cmp-1' }, null]) {
    assert.equal(refable(it), false, JSON.stringify(it));
  }
});

test('partAt counts earlier items with the same uuid, from an optional start', () => {
  const items = [{ uuid: 'u1' }, { uuid: 'a1' }, { uuid: 'a1' }, { uuid: 'a1' }, { uuid: 'u2' }];
  assert.deepEqual(items.map((_, i) => partAt(items, i)), [0, 0, 1, 2, 0]);
  assert.equal(partAt(items, 3, 1), 2, 'starting at the first a1 gives the same answer');
});

test('messageRef', () => {
  assert.equal(messageRef(S, 'a1', 1), `${S}~a1#1`);
});

test('stampRefs: copies with refs on refable items, the rest untouched, input not mutated', () => {
  const items: any[] = [
    { kind: 'text', role: 'user', uuid: 'u1' },
    { kind: 'text', role: 'assistant', uuid: 'a1' },
    { kind: 'tool_use', uuid: 'a1' },
    { kind: 'text', role: 'user', uuid: 'interrupted:x1' },
    { kind: 'text', role: 'assistant' },
    { kind: 'text', role: 'assistant', uuid: 3 },
  ];
  const out = stampRefs(items, S);
  assert.deepEqual(out.map((i) => i.ref ?? null), [`${S}~u1#0`, `${S}~a1#0`, `${S}~a1#1`, null, null, `${S}~3#0`]);
  assert.equal(items[0].ref, undefined, 'the input is not mutated');
  assert.equal(out[3], items[3], 'an item with no ref is passed through as is');
});

test('stampRefs without a session: nothing stamped (no stable identity)', () => {
  const items = [{ kind: 'text', uuid: 'u1' }];
  assert.equal(stampRefs(items, null), items);
  assert.equal(stampRefs(items, ''), items);
});
