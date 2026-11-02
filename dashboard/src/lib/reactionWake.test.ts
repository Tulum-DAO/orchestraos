// The reaction wake (api chat-transcript reaction_wake): an empty flagged user turn the chat never draws. The agent's
// reply right after it is marked afterReaction, so an emoji-only answer to the reaction is not folded into the
// operator's earlier message as if it reacted to that (view.ts withReactions).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildRenderList, type ChatItem } from './transcript';
import { agentRows } from './threadKit/view';
import type { FeedEntry } from './decisions';

const WAKE_ITEMS = [
  { kind: 'text', role: 'user', text: 'Ship it', uuid: 'u1' },
  { kind: 'text', role: 'user', text: '', uuid: 'w1', reaction_wake: true },
  { kind: 'text', role: 'assistant', text: '🙏', uuid: 'a1' },
  { kind: 'text', role: 'assistant', text: 'Done.', uuid: 'a2' },
] as unknown as ChatItem[];

test('a reaction wake is never drawn, and only the reply right after it is marked', () => {
  const nodes = buildRenderList(WAKE_ITEMS);
  assert.deepEqual(nodes.map((n) => [n.key, n.kind, (n as { afterReaction?: boolean }).afterReaction ?? false]),
    [['u1:0', 'user', false], ['a1:0', 'assistant', true], ['a2:0', 'assistant', false]]);
});

test('a wake followed by thinking marks nothing (the next node is not a reply)', () => {
  const nodes = buildRenderList([
    { kind: 'text', role: 'user', text: '', uuid: 'w1', reaction_wake: true },
    { kind: 'thinking', text: 'hm', uuid: 't1' },
    { kind: 'text', role: 'assistant', text: '👍', uuid: 'a1' },
  ] as unknown as ChatItem[]);
  assert.ok(nodes.every((n) => !(n as { afterReaction?: boolean }).afterReaction));
});

const rowsOf = (items: ChatItem[]) => agentRows(buildRenderList(items).map((node) => ({ kind: 'node', node }) as FeedEntry), [], 'idle');
const reactions = (items: ChatItem[]) => rowsOf(items).map((r) => [r.key, (r.kind === 'node' ? r.reaction : undefined) ?? null]);

test('an emoji answer to a hidden reaction turn stays a row; the operator\'s message gets no reaction', () => {
  assert.deepEqual(reactions(WAKE_ITEMS), [['u1:0', null], ['a1:0', null], ['a2:0', null]]);
});

test('old logs: a 👍 reply right after the operator\'s message still folds into it', () => {
  assert.deepEqual(reactions([
    { kind: 'text', role: 'user', text: 'Test', uuid: 'u1' },
    { kind: 'text', role: 'assistant', text: '👍', uuid: 'a1' },
  ] as unknown as ChatItem[]), [['u1:0', '👍']]);
});
