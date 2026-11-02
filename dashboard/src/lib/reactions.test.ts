// The agent's emoji-only reply right after the operator's message is drawn as a reaction under it (the reference
// call, 2026-11-02: "Test" and "Another test" each got a 👍, not a reply).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isReaction, withReactions, type ThreadRow } from './threadKit/view';
import { buildRenderList, type ChatItem } from './transcript';

const user = (key: string, text: string) => ({ kind: 'node', key, interrupted: false, node: { kind: 'user', text, key } }) as unknown as ThreadRow;
const agent = (key: string, text: string) => ({ kind: 'node', key, interrupted: false, node: { kind: 'assistant', text, key } }) as unknown as ThreadRow;

test('isReaction: an emoji or two, nothing written', () => {
  for (const t of ['👍', ' 👍 ', '👍🏽', '🎉👍', '❤️']) assert.equal(isReaction(t), true, t);
  for (const t of ['ok 👍', 'yes', '1', '', '...', '👍👍👍👍👍👍👍👍👍', undefined]) assert.equal(isReaction(t), false, String(t));
});

test('a 👍 reply becomes the previous message\'s reaction', () => {
  const rows = withReactions([user('u1', 'Test'), agent('a1', '👍'), user('u2', 'Another test'), agent('a2', '👍')]);
  assert.deepEqual(rows.map((r) => [r.key, r.kind === 'node' ? r.reaction : undefined]), [['u1', '👍'], ['u2', '👍']]);
});

test('a reply with words, a reaction not after the operator, and a second reaction stay rows', () => {
  const rows = withReactions([agent('a0', '👍'), user('u1', 'hi'), agent('a1', 'Hello 👍'), user('u2', 'x'), agent('a2', '👍'), agent('a3', '🎉')]);
  assert.deepEqual(rows.map((r) => [r.key, (r.kind === 'node' ? r.reaction : undefined) ?? null]),
    [['a0', null], ['u1', null], ['a1', null], ['u2', '👍'], ['a3', null]]);
});

test('a request said on a voice call is not drawn as a bubble; the reply is', () => {
  const nodes = buildRenderList([
    { kind: 'text', role: 'user', text: 'send me a note', uuid: 'u1', voice_said: true },
    { kind: 'text', role: 'assistant', text: 'Sent.', uuid: 'a1' },
  ] as unknown as ChatItem[]);
  assert.deepEqual(nodes.map((n) => n.kind), ['assistant']);
});
