// Reactions on an agent's chat rows (reactions build step 2): the server's ref rides each message node, agentRows lays
// the stored reactions onto the rows by ref, and an OLD log's emoji-only reply is still folded into a badge, but never
// onto a message that already has a stored reaction.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildRenderList, type ChatItem, type RenderNode } from './transcript.ts';
import { agentRows, reactRef, rowReactions, withReactions, type NodeRow, type ThreadRow } from './threadKit/view.ts';
import type { FeedEntry } from './decisions.ts';
import type { ReactionMap } from './reactionState.ts';

const U1 = '11111111-2222-4333-8444-000000000061';
const U2 = '11111111-2222-4333-8444-000000000062';
const sam = { kind: 'user', id: 'sam' } as const;
const feed = (nodes: RenderNode[]): FeedEntry[] => nodes.map((node) => ({ kind: 'node', node }));
const nodeRows = (rows: ThreadRow[]) => rows.filter((r): r is NodeRow => r.kind === 'node');

test('buildRenderList keeps the server\'s ref on the operator\'s words and the agent\'s replies', () => {
  const nodes = buildRenderList([
    { kind: 'text', role: 'user', text: 'hello', uuid: U1, ref: `s1~${U1}#0` },
    { kind: 'text', role: 'assistant', text: 'Hi.', uuid: U2, ref: `s1~${U2}#0` },
    { kind: 'text', role: 'assistant', text: 'No ref here.', uuid: 'x' },
  ] as unknown as ChatItem[]);
  assert.deepEqual(nodes.map((n) => ('ref' in n ? n.ref : undefined)), [`s1~${U1}#0`, `s1~${U2}#0`, undefined]);
});

test('only plain words carry a react ref: never a session prompt, a machine line, a queued send, a call marker or a paste', () => {
  const ref = `s1~${U1}#0`;
  assert.equal(reactRef({ kind: 'user', text: 'hello', key: 'k', ref }), ref);
  assert.equal(reactRef({ kind: 'assistant', text: 'Hi.', key: 'k', ref }), ref);
  assert.equal(reactRef({ kind: 'assistant', text: 'Hi.', key: 'k' }), undefined);
  assert.equal(reactRef({ kind: 'user', text: 'You are gm…', key: 'k', ref, isSystem: true }), undefined);
  assert.equal(reactRef({ kind: 'user', text: 'x', key: 'k', ref, machine: 'relay' }), undefined);
  assert.equal(reactRef({ kind: 'user', text: 'x', key: 'k', ref, queued: true }), undefined);
  assert.equal(reactRef({ kind: 'user', text: '[voice-call: vc_sample_call01 /data/x.json]', key: 'k', ref }), undefined);
  assert.equal(reactRef({ kind: 'thinking', text: 'hm', key: 'k' }), undefined);
});

test('agentRows lays stored reactions onto their rows by ref; a row without any has a ref and no reactions', () => {
  const r1 = `s1~${U1}#0`;
  const r2 = `s1~${U2}#0`;
  const reactions: ReactionMap = { [r2]: [{ emoji: '👍', reactor: sam, at: 't' }] };
  const rows = nodeRows(agentRows(feed([
    { kind: 'user', text: 'hello', key: 'a', ref: r1 },
    { kind: 'assistant', text: 'Hi.', key: 'b', ref: r2 },
    { kind: 'thinking', text: 'hm', key: 'c' },
  ]), [], 'idle', { reactions }));
  assert.deepEqual(rows.map((r) => [r.key, r.ref ?? null, r.reactions?.map((x) => x.emoji) ?? null]),
    [['a', r1, null], ['b', r2, ['👍']], ['c', null, null]]);
  // with no map at all, refs still ride (the row can be reacted to before the first read lands)
  assert.equal(nodeRows(agentRows(feed([{ kind: 'user', text: 'hello', key: 'a', ref: r1 }]), [], 'idle'))[0].ref, r1);
});

test('a carried-over message shows, and writes to, the reaction stored under its older copy', () => {
  const old = `s1~${U1}#0`;
  const now = `s2~${U1}#0`;
  const rows = nodeRows(agentRows(feed([{ kind: 'user', text: 'hello', key: 'a', ref: now }]), [], 'idle',
    { reactions: { [old]: [{ emoji: '🎉', reactor: sam, at: 't' }] } }));
  assert.equal(rows[0].ref, old);
  assert.deepEqual(rows[0].reactions?.map((x) => x.emoji), ['🎉']);
});

test('an old log\'s 👍 reply still folds into a badge, but not onto a message with a stored reaction', () => {
  const user = (key: string, ref?: string) => ({ kind: 'node', key, interrupted: false, node: { kind: 'user', text: 'hi', key }, ...(ref ? { ref } : {}) }) as ThreadRow;
  const agent = (key: string) => ({ kind: 'node', key, interrupted: false, node: { kind: 'assistant', text: '👍', key } }) as ThreadRow;
  const stored = { ...(user('u2') as NodeRow), reactions: [{ emoji: '❤️', reactor: sam, at: 't' }] } as ThreadRow;
  const rows = withReactions([user('u1'), agent('a1'), stored, agent('a2')]);
  assert.deepEqual(rows.map((r) => [r.key, r.kind === 'node' ? r.reaction ?? null : null]), [['u1', '👍'], ['u2', null], ['a2', null]]);
});

test('a row\'s badges: the stored reactions, else the folded emoji as the agent\'s own, else none', () => {
  const base = { kind: 'node', key: 'k', interrupted: false, node: { kind: 'user', text: 'hi', key: 'k' } } as NodeRow;
  assert.deepEqual(rowReactions({ ...base, reaction: '👍' }, 'pm-x'), [{ emoji: '👍', reactor: { kind: 'agent', id: 'pm-x' }, at: '' }]);
  const list = [{ emoji: '❤️', reactor: sam, at: 't' }];
  assert.equal(rowReactions({ ...base, reaction: '👍', reactions: list }, 'pm-x'), list);
  assert.deepEqual(rowReactions(base, 'pm-x'), []);
});

test('an old log\'s 👍 and stored reactions never both show: through agentRows, the stored ones win on their ref', () => {
  const r1 = `s1~${U1}#0`;
  const r2 = `s1~${U2}#0`;
  const U3 = '11111111-2222-4333-8444-000000000063';
  const U4 = '11111111-2222-4333-8444-000000000064';
  const reactions: ReactionMap = { [r1]: [{ emoji: '❤️', reactor: sam, at: 't' }] };
  const rows = nodeRows(agentRows(feed([
    { kind: 'user', text: 'one', key: 'a', ref: r1 },
    { kind: 'assistant', text: '👍', key: 'b', ref: `s1~${U3}#0` },
    { kind: 'user', text: 'two', key: 'c', ref: r2 },
    { kind: 'assistant', text: '👍', key: 'd', ref: `s1~${U4}#0` },
  ]), [], 'idle', { reactions }));
  const shown = (key: string) => rowReactions(rows.find((r) => r.key === key)!, 'pm-x').map((x) => `${x.reactor.kind}:${x.emoji}`);
  assert.deepEqual(shown('a'), ['user:❤️']);      // stored: the old 👍 is not added to it
  assert.deepEqual(shown('c'), ['agent:👍']);      // nothing stored: the old 👍 shows as the agent's own
});
